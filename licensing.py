import base64
import hashlib
import json
import os
import platform
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "PortalCalc"
LICENSE_FILE = "license_token.json"
DEFAULT_LICENSE_ENFORCED = True
DEFAULT_LICENSE_API_BASE_URL = "https://portalcalc.onrender.com"
DEFAULT_LICENSE_PUBLIC_KEY_HEX = "07b75c5894be9353274df66ae71a06150ac1e0272a967fb1dcab928032e56776"


@dataclass
class LicenseStatus:
    valid: bool
    reason: str
    customer: str = ""
    plan: str = ""
    expires_at: int | None = None
    days_remaining: int | None = None


class LicenseError(Exception):
    pass


def license_enforced() -> bool:
    value = os.environ.get("PORTALCALC_LICENSE_ENFORCED")
    if value is None:
        return DEFAULT_LICENSE_ENFORCED
    return value.strip().lower() in {"1", "true", "yes", "on"}


def app_data_dir() -> Path:
    root = os.environ.get("APPDATA")
    if root:
        path = Path(root) / APP_NAME
    else:
        path = Path.home() / ".portalcalc"
    path.mkdir(parents=True, exist_ok=True)
    return path


def license_path() -> Path:
    return app_data_dir() / LICENSE_FILE


def machine_id() -> str:
    raw = f"{platform.node()}|{uuid.getnode()}|{platform.system()}|{platform.machine()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _json_post(url: str, payload: dict, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LicenseError(str(exc)) from exc


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    _load_windows_root_certificates(context)
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _load_windows_root_certificates(context: ssl.SSLContext) -> None:
    enum_certificates = getattr(ssl, "enum_certificates", None)
    der_to_pem = getattr(ssl, "DER_cert_to_PEM_cert", None)
    if enum_certificates is None or der_to_pem is None:
        return
    try:
        for cert_bytes, encoding, trust in enum_certificates("ROOT"):
            if encoding != "x509_asn":
                continue
            if trust is not True and "1.3.6.1.5.5.7.3.1" not in trust:
                continue
            context.load_verify_locations(cadata=der_to_pem(cert_bytes))
    except Exception:
        return


class LicenseManager:
    """
    Local license helper.

    Tokens use a compact format: base64url(json_payload).base64url(signature).
    The server signs the JSON payload with Ed25519; the app verifies it using
    the public key embedded/configured locally.
    """

    def __init__(self, api_base_url: str = "", public_key_hex: str = "", storage_path: Path | None = None):
        self.api_base_url = (
            api_base_url
            or os.environ.get("PORTALCALC_LICENSE_API", "")
            or DEFAULT_LICENSE_API_BASE_URL
        ).rstrip("/")
        self.public_key_hex = (
            public_key_hex
            or os.environ.get("PORTALCALC_LICENSE_PUBLIC_KEY", "")
            or DEFAULT_LICENSE_PUBLIC_KEY_HEX
        )
        self.storage_path = storage_path or license_path()

    @property
    def activation_url(self) -> str:
        return f"{self.api_base_url}/activate"

    @property
    def check_url(self) -> str:
        return f"{self.api_base_url}/check"

    def read_token(self) -> str | None:
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        token = data.get("token")
        return str(token) if token else None

    def write_token(self, token: str) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_path.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")

    def verify_token(self, token: str) -> dict:
        if not self.public_key_hex:
            raise LicenseError("License public key is not configured.")
        try:
            payload_b64, signature_b64 = token.split(".", 1)
            payload_bytes = _b64url_decode(payload_b64)
            signature = _b64url_decode(signature_b64)
            public_key_bytes = bytes.fromhex(self.public_key_hex)
        except (ValueError, TypeError) as exc:
            raise LicenseError("Malformed license token.") from exc

        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError as exc:
            raise LicenseError("cryptography package is required for license verification.") from exc

        try:
            public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(signature, payload_bytes)
            return json.loads(payload_bytes.decode("utf-8"))
        except Exception as exc:
            raise LicenseError("License token signature is invalid.") from exc

    def status_from_payload(self, payload: dict) -> LicenseStatus:
        now = int(time.time())
        machine = payload.get("machine_id")
        if machine and machine != machine_id():
            return LicenseStatus(False, "License is activated for another machine.")
        expires_at = payload.get("expires_at")
        days_remaining = None
        if expires_at is not None:
            days_remaining = int((int(expires_at) - now) / 86400)
            if int(expires_at) < now:
                return LicenseStatus(False, "License has expired.", expires_at=int(expires_at), days_remaining=days_remaining)
        return LicenseStatus(
            True,
            "Valid",
            customer=str(payload.get("customer", "")),
            plan=str(payload.get("plan", "")),
            expires_at=int(expires_at) if expires_at is not None else None,
            days_remaining=days_remaining,
        )

    def local_status(self) -> LicenseStatus:
        token = self.read_token()
        if not token:
            return LicenseStatus(False, "No license is activated.")
        try:
            return self.status_from_payload(self.verify_token(token))
        except LicenseError as exc:
            return LicenseStatus(False, str(exc))

    def activate(self, license_key: str) -> LicenseStatus:
        if not self.api_base_url:
            return LicenseStatus(False, "License API URL is not configured.")
        response = _json_post(self.activation_url, {"license_key": license_key, "machine_id": machine_id()})
        token = response.get("token")
        if not token:
            return LicenseStatus(False, response.get("message", "Activation failed."))
        payload = self.verify_token(str(token))
        status = self.status_from_payload(payload)
        if status.valid:
            self.write_token(str(token))
        return status

    def refresh(self) -> LicenseStatus:
        token = self.read_token()
        if not token:
            return LicenseStatus(False, "No license is activated.")
        if not self.api_base_url:
            return self.local_status()
        response = _json_post(self.check_url, {"token": token, "machine_id": machine_id()})
        if response.get("revoked"):
            return LicenseStatus(False, response.get("message", "License has been revoked."))
        refreshed = response.get("token")
        if refreshed:
            self.write_token(str(refreshed))
        return self.local_status()


def require_valid_license(parent=None, manager: LicenseManager | None = None) -> bool:
    manager = manager or LicenseManager()
    status = manager.local_status()
    if status.valid:
        return True

    try:
        from PySide6.QtWidgets import QInputDialog, QMessageBox
    except ImportError:
        return False

    key, accepted = QInputDialog.getText(parent, "Activate License", f"{status.reason}\n\nEnter license key:")
    if not accepted or not key.strip():
        return False
    activated = manager.activate(key.strip())
    if not activated.valid:
        QMessageBox.critical(parent, "License activation failed", activated.reason)
        return False
    return True
