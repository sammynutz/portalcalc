import argparse
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


DEFAULT_DB_PATH = "portalcalc_licenses.db"
DEFAULT_TOKEN_DAYS = 30


@dataclass
class ServerSettings:
    db_path: str = DEFAULT_DB_PATH
    private_key_hex: str = ""
    admin_token: str = ""
    token_days: int = DEFAULT_TOKEN_DAYS


def load_settings() -> ServerSettings:
    return ServerSettings(
        db_path=os.environ.get("PORTALCALC_LICENSE_DB", DEFAULT_DB_PATH),
        private_key_hex=os.environ.get("PORTALCALC_LICENSE_PRIVATE_KEY", ""),
        admin_token=os.environ.get("PORTALCALC_LICENSE_ADMIN_TOKEN", ""),
        token_days=int(os.environ.get("PORTALCALC_LICENSE_TOKEN_DAYS", str(DEFAULT_TOKEN_DAYS))),
    )


def utc_now() -> int:
    return int(time.time())


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def generate_keypair() -> tuple[str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_bytes.hex(), public_bytes.hex()


def private_key_from_hex(private_key_hex: str) -> Ed25519PrivateKey:
    if not private_key_hex:
        raise RuntimeError("PORTALCALC_LICENSE_PRIVATE_KEY is not configured.")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))


def public_key_hex_from_private(private_key_hex: str) -> str:
    public_key = private_key_from_hex(private_key_hex).public_key()
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_bytes.hex()


def sign_payload(payload: dict[str, Any], private_key_hex: str) -> str:
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = private_key_from_hex(private_key_hex).sign(payload_bytes)
    return f"{b64url(payload_bytes)}.{b64url(signature)}"


def verify_token(token: str, public_key_hex: str) -> dict[str, Any]:
    payload_b64, signature_b64 = token.split(".", 1)
    payload_bytes = b64url_decode(payload_b64)
    signature = b64url_decode(signature_b64)
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
    public_key.verify(signature, payload_bytes)
    return json.loads(payload_bytes.decode("utf-8"))


def hash_license_key(license_key: str) -> str:
    return hashlib.sha256(license_key.encode("utf-8")).hexdigest()


def make_license_key(prefix: str = "PC") -> str:
    return f"{prefix}-{secrets.token_urlsafe(18).replace('_', '').replace('-', '').upper()[:24]}"


def connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def db_session(db_path: str):
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True) if Path(db_path).parent != Path(".") else None
    with db_session(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                license_key_hash TEXT PRIMARY KEY,
                customer TEXT NOT NULL,
                plan TEXT NOT NULL,
                expires_at INTEGER,
                max_machines INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activations (
                license_key_hash TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                activated_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                PRIMARY KEY (license_key_hash, machine_id),
                FOREIGN KEY (license_key_hash) REFERENCES licenses (license_key_hash)
            );
            """
        )


def create_license(
    db_path: str,
    customer: str,
    plan: str,
    expires_at: int | None,
    max_machines: int = 1,
    license_key: str | None = None,
) -> str:
    license_key = license_key or make_license_key()
    with db_session(db_path) as db:
        db.execute(
            """
            INSERT INTO licenses (
                license_key_hash, customer, plan, expires_at, max_machines, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (hash_license_key(license_key), customer, plan, expires_at, max_machines, utc_now()),
        )
    return license_key


def license_row(db: sqlite3.Connection, license_key_hash: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM licenses WHERE license_key_hash = ?",
        (license_key_hash,),
    ).fetchone()


def activation_count(db: sqlite3.Connection, license_key_hash: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS count FROM activations WHERE license_key_hash = ?",
        (license_key_hash,),
    ).fetchone()
    return int(row["count"]) if row else 0


def build_token_payload(row: sqlite3.Row, license_key_hash: str, machine_id: str) -> dict[str, Any]:
    return {
        "license_key_hash": license_key_hash,
        "machine_id": machine_id,
        "customer": row["customer"],
        "plan": row["plan"],
        "expires_at": row["expires_at"],
        "issued_at": utc_now(),
    }


def activate_license(settings: ServerSettings, license_key: str, machine_id: str) -> dict[str, Any]:
    license_key_hash = hash_license_key(license_key.strip())
    now = utc_now()
    with db_session(settings.db_path) as db:
        row = license_row(db, license_key_hash)
        if row is None:
            return {"ok": False, "message": "License key was not found."}
        if row["status"] != "active":
            return {"ok": False, "message": "License has been revoked."}
        if row["expires_at"] is not None and int(row["expires_at"]) < now:
            return {"ok": False, "message": "License has expired."}

        existing = db.execute(
            "SELECT 1 FROM activations WHERE license_key_hash = ? AND machine_id = ?",
            (license_key_hash, machine_id),
        ).fetchone()
        if existing is None and activation_count(db, license_key_hash) >= int(row["max_machines"]):
            return {"ok": False, "message": "License has reached its machine limit."}

        db.execute(
            """
            INSERT INTO activations (license_key_hash, machine_id, activated_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(license_key_hash, machine_id)
            DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (license_key_hash, machine_id, now, now),
        )
        token = sign_payload(build_token_payload(row, license_key_hash, machine_id), settings.private_key_hex)
        return {"ok": True, "token": token}


def check_license(settings: ServerSettings, token: str, machine_id: str) -> dict[str, Any]:
    try:
        public_key_hex = public_key_hex_from_private(settings.private_key_hex)
        payload = verify_token(token, public_key_hex)
    except Exception:
        return {"revoked": True, "message": "License token is invalid."}

    license_key_hash = str(payload.get("license_key_hash", ""))
    if str(payload.get("machine_id", "")) != machine_id:
        return {"revoked": True, "message": "License is activated for another machine."}

    now = utc_now()
    with db_session(settings.db_path) as db:
        row = license_row(db, license_key_hash)
        if row is None or row["status"] != "active":
            return {"revoked": True, "message": "License has been revoked."}
        if row["expires_at"] is not None and int(row["expires_at"]) < now:
            return {"revoked": True, "message": "License has expired."}
        existing = db.execute(
            "SELECT 1 FROM activations WHERE license_key_hash = ? AND machine_id = ?",
            (license_key_hash, machine_id),
        ).fetchone()
        if existing is None:
            return {"revoked": True, "message": "Machine is not activated for this license."}
        db.execute(
            "UPDATE activations SET last_seen_at = ? WHERE license_key_hash = ? AND machine_id = ?",
            (now, license_key_hash, machine_id),
        )
        return {"revoked": False, "token": sign_payload(build_token_payload(row, license_key_hash, machine_id), settings.private_key_hex)}


def create_app(settings: ServerSettings | None = None):
    try:
        from fastapi import FastAPI, Header, HTTPException
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("Install fastapi and uvicorn to run the license server.") from exc

    settings = settings or load_settings()
    app = FastAPI(title="PortalCalc License API", version="0.1.0")

    class ActivateRequest(BaseModel):
        license_key: str
        machine_id: str

    class CheckRequest(BaseModel):
        token: str
        machine_id: str

    class CreateLicenseRequest(BaseModel):
        customer: str
        plan: str = "standard"
        expires_at: int | None = None
        max_machines: int = 1

    def require_admin(x_admin_token: str | None) -> None:
        if not settings.admin_token:
            raise HTTPException(status_code=403, detail="Admin API is disabled until PORTALCALC_LICENSE_ADMIN_TOKEN is set")
        if x_admin_token != settings.admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")

    @app.on_event("startup")
    def startup() -> None:
        init_db(settings.db_path)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/public-key")
    def public_key() -> dict[str, Any]:
        return {"public_key_hex": public_key_hex_from_private(settings.private_key_hex)}

    @app.post("/activate")
    def activate(request: ActivateRequest) -> dict[str, Any]:
        return activate_license(settings, request.license_key, request.machine_id)

    @app.post("/check")
    def check(request: CheckRequest) -> dict[str, Any]:
        return check_license(settings, request.token, request.machine_id)

    @app.post("/admin/licenses")
    def admin_create_license(
        request: CreateLicenseRequest,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        license_key = create_license(
            settings.db_path,
            customer=request.customer,
            plan=request.plan,
            expires_at=request.expires_at,
            max_machines=request.max_machines,
        )
        return {"license_key": license_key}

    return app


def parse_expiry(value: str) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    from datetime import datetime, timezone

    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description="PortalCalc licensing server utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("keygen", help="Generate Ed25519 private/public keys.")

    init_parser = subparsers.add_parser("init-db", help="Create the SQLite license database.")
    init_parser.add_argument("--db", default=os.environ.get("PORTALCALC_LICENSE_DB", DEFAULT_DB_PATH))

    create_parser = subparsers.add_parser("create-license", help="Create a license key in the database.")
    create_parser.add_argument("--db", default=os.environ.get("PORTALCALC_LICENSE_DB", DEFAULT_DB_PATH))
    create_parser.add_argument("--customer", required=True)
    create_parser.add_argument("--plan", default="standard")
    create_parser.add_argument("--expires-at", default="", help="Unix timestamp or ISO date, blank for no expiry.")
    create_parser.add_argument("--max-machines", type=int, default=1)

    args = parser.parse_args()
    if args.command == "keygen":
        private_hex, public_hex = generate_keypair()
        print(f"PORTALCALC_LICENSE_PRIVATE_KEY={private_hex}")
        print(f"PORTALCALC_LICENSE_PUBLIC_KEY={public_hex}")
    elif args.command == "init-db":
        init_db(args.db)
        print(f"Created/updated {args.db}")
    elif args.command == "create-license":
        init_db(args.db)
        license_key = create_license(
            args.db,
            customer=args.customer,
            plan=args.plan,
            expires_at=parse_expiry(args.expires_at),
            max_machines=args.max_machines,
        )
        print(license_key)


try:
    app = create_app()
except Exception:
    app = None


if __name__ == "__main__":
    main()
