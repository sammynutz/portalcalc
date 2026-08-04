import argparse
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


DEFAULT_DB_PATH = "portalcalc_licenses.db"
DEFAULT_TOKEN_DAYS = 30
DEFAULT_PERSISTENT_ROOT = "/var/data"
_BACKUP_LOCK = threading.Lock()
ADMIN_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PortalCalc License Admin</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: Arial, Helvetica, sans-serif;
      --border: #d7dde5;
      --muted: #5f6b7a;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #17202c;
      --accent: #1769aa;
      --danger: #b42318;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 20px 24px 40px;
    }
    .toolbar, .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }
    .toolbar {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr)) auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 16px;
    }
    label {
      display: grid;
      gap: 5px;
      font-size: 12px;
      color: var(--muted);
    }
    input, button, select {
      min-height: 36px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0 10px;
      font: inherit;
    }
    input, select {
      background: transparent;
      color: inherit;
    }
    button {
      cursor: pointer;
      background: #eef4fa;
      color: var(--text);
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
    }
    button.danger {
      background: #fff1f0;
      border-color: #ffccc7;
      color: var(--danger);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      padding: 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      font-size: 14px;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    tr:last-child td {
      border-bottom: 0;
    }
    .status {
      display: inline-block;
      min-width: 66px;
      border-radius: 999px;
      padding: 3px 8px;
      text-align: center;
      font-size: 12px;
      background: #e6f4ea;
      color: #137333;
    }
    .status.canceled {
      background: #fce8e6;
      color: var(--danger);
    }
    .muted {
      color: var(--muted);
    }
    .hash {
      font-family: Consolas, monospace;
      font-size: 12px;
      word-break: break-all;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    dialog {
      width: min(740px, calc(100vw - 32px));
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0;
      color: var(--text);
      background: var(--panel);
    }
    dialog::backdrop {
      background: rgba(0, 0, 0, .35);
    }
    .dialog-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
    }
    .dialog-body {
      padding: 16px;
    }
    #message {
      margin: 0 0 12px;
      min-height: 20px;
      color: var(--muted);
    }
    @media (max-width: 860px) {
      header {
        align-items: stretch;
        flex-direction: column;
      }
      .toolbar {
        grid-template-columns: 1fr;
      }
      table, thead, tbody, th, td, tr {
        display: block;
      }
      thead {
        display: none;
      }
      tr {
        border-bottom: 1px solid var(--border);
        padding: 8px 0;
      }
      td {
        border: 0;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>PortalCalc License Admin</h1>
    <label>Admin token
      <input id="token" type="password" autocomplete="current-password">
    </label>
  </header>
  <main>
    <p id="message"></p>
    <section class="toolbar">
      <label>Customer
        <input id="customer" placeholder="Customer name">
      </label>
      <label>Plan
        <input id="plan" value="standard">
      </label>
      <label>Expires
        <input id="expires" type="date">
      </label>
      <label>Machines
        <input id="machines" type="number" min="1" value="1">
      </label>
      <span></span>
      <button id="create" class="primary">Create License</button>
    </section>
    <table>
      <thead>
        <tr>
          <th>Customer</th>
          <th>Plan</th>
          <th>Status</th>
          <th>Activations</th>
          <th>Expires</th>
          <th>Created</th>
          <th>Hash</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="licenses"></tbody>
    </table>
  </main>
  <dialog id="detailDialog">
    <div class="dialog-head">
      <strong id="detailTitle">License</strong>
      <button id="closeDetail">Close</button>
    </div>
    <div class="dialog-body">
      <div id="detailBody"></div>
    </div>
  </dialog>
  <script>
    const tokenInput = document.querySelector("#token");
    const message = document.querySelector("#message");
    const tbody = document.querySelector("#licenses");
    const detailDialog = document.querySelector("#detailDialog");
    const detailBody = document.querySelector("#detailBody");
    const detailTitle = document.querySelector("#detailTitle");
    tokenInput.value = sessionStorage.getItem("portalcalcAdminToken") || "";

    function headers() {
      const token = tokenInput.value.trim();
      sessionStorage.setItem("portalcalcAdminToken", token);
      return {"Content-Type": "application/json", "X-Admin-Token": token};
    }
    function fmtDate(seconds) {
      if (!seconds) return "Never";
      return new Date(seconds * 1000).toLocaleString();
    }
    function setMessage(text, isError) {
      message.textContent = text || "";
      message.style.color = isError ? "#b42318" : "";
    }
    async function api(path, options = {}) {
      const response = await fetch(path, {...options, headers: {...headers(), ...(options.headers || {})}});
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return await response.json();
    }
    async function loadLicenses() {
      try {
        const data = await api("/admin/licenses");
        tbody.innerHTML = "";
        data.licenses.forEach((license) => {
          const row = document.createElement("tr");
          const statusClass = license.status === "active" ? "" : "canceled";
          row.innerHTML = `
            <td>${escapeHtml(license.customer)}</td>
            <td>${escapeHtml(license.plan)}</td>
            <td><span class="status ${statusClass}">${escapeHtml(license.status)}</span></td>
            <td>${license.activations} / ${license.max_machines}</td>
            <td>${fmtDate(license.expires_at)}</td>
            <td>${fmtDate(license.created_at)}</td>
            <td class="hash">${license.license_key_hash_short}</td>
            <td><div class="actions"></div></td>
          `;
          const actions = row.querySelector(".actions");
          addButton(actions, "View", () => showDetail(license.license_key_hash));
          addButton(actions, "Edit", () => editLicense(license));
          if (license.status === "active") {
            addButton(actions, "Cancel", () => setStatus(license.license_key_hash, "cancel"), "danger");
          } else {
            addButton(actions, "Reactivate", () => setStatus(license.license_key_hash, "reactivate"));
          }
          tbody.appendChild(row);
        });
        setMessage(`Loaded ${data.licenses.length} license(s).`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    function addButton(parent, text, handler, className) {
      const button = document.createElement("button");
      button.textContent = text;
      if (className) button.className = className;
      button.addEventListener("click", handler);
      parent.appendChild(button);
    }
    async function createLicense() {
      const expires = document.querySelector("#expires").value;
      const payload = {
        customer: document.querySelector("#customer").value.trim(),
        plan: document.querySelector("#plan").value.trim() || "standard",
        expires_at: expires ? Math.floor(new Date(`${expires}T23:59:59Z`).getTime() / 1000) : null,
        max_machines: Number(document.querySelector("#machines").value || 1),
      };
      if (!payload.customer) {
        setMessage("Customer is required.", true);
        return;
      }
      try {
        const created = await api("/admin/licenses", {method: "POST", body: JSON.stringify(payload)});
        await loadLicenses();
        setMessage(`Created license key: ${created.license_key}`);
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    async function setStatus(hash, action) {
      if (action === "cancel" && !confirm("Cancel this license? Existing desktop tokens will fail on their next server check.")) return;
      try {
        await api(`/admin/licenses/${hash}/${action}`, {method: "POST"});
        await loadLicenses();
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    async function editLicense(license) {
      const customer = prompt("Customer", license.customer || "");
      if (customer === null) return;
      const plan = prompt("Plan", license.plan || "standard");
      if (plan === null) return;
      const machinesText = prompt("Max machines", license.max_machines || 1);
      if (machinesText === null) return;
      const currentExpiry = license.expires_at ? new Date(license.expires_at * 1000).toISOString().slice(0, 10) : "";
      const expiryText = prompt("Expiry date (YYYY-MM-DD, blank for none)", currentExpiry);
      if (expiryText === null) return;
      const payload = {
        customer: customer.trim(),
        plan: plan.trim() || "standard",
        expires_at: expiryText.trim() ? Math.floor(new Date(`${expiryText.trim()}T23:59:59Z`).getTime() / 1000) : null,
        max_machines: Number(machinesText || 1),
      };
      if (!payload.customer) {
        setMessage("Customer is required.", true);
        return;
      }
      if (!Number.isFinite(payload.max_machines) || payload.max_machines < 1) {
        setMessage("Max machines must be at least 1.", true);
        return;
      }
      try {
        await api(`/admin/licenses/${license.license_key_hash}`, {method: "PATCH", body: JSON.stringify(payload)});
        await loadLicenses();
        setMessage("License updated.");
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    async function showDetail(hash) {
      try {
        const detail = await api(`/admin/licenses/${hash}`);
        detailTitle.textContent = `${detail.customer} (${detail.status})`;
        const activations = detail.activation_rows.map((activation) => `
          <tr>
            <td>
              <strong>${escapeHtml(activation.machine_name || "Unnamed computer")}</strong>
              <div class="muted hash">${escapeHtml(activation.machine_id)}</div>
            </td>
            <td>${fmtDate(activation.activated_at)}</td>
            <td>${fmtDate(activation.last_seen_at)}</td>
            <td><button class="danger" data-machine="${escapeHtml(activation.machine_id)}">Remove</button></td>
          </tr>
        `).join("");
        detailBody.innerHTML = `
          <p class="muted hash">${detail.license_key_hash}</p>
          <table>
            <thead><tr><th>Machine</th><th>Activated</th><th>Last seen</th><th>Actions</th></tr></thead>
            <tbody>${activations || '<tr><td colspan="4" class="muted">No activations yet.</td></tr>'}</tbody>
          </table>
        `;
        detailBody.querySelectorAll("[data-machine]").forEach((button) => {
          button.addEventListener("click", () => removeActivation(hash, button.dataset.machine));
        });
        detailDialog.showModal();
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    async function removeActivation(hash, machine) {
      if (!confirm("Remove this machine activation? The customer can activate another machine if capacity allows.")) return;
      try {
        await api(`/admin/licenses/${hash}/activations/${machine}`, {method: "DELETE"});
        await showDetail(hash);
        await loadLicenses();
      } catch (error) {
        setMessage(error.message, true);
      }
    }
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char]));
    }
    document.querySelector("#create").addEventListener("click", createLicense);
    document.querySelector("#closeDetail").addEventListener("click", () => detailDialog.close());
    tokenInput.addEventListener("change", loadLicenses);
    if (tokenInput.value) loadLicenses();
  </script>
</body>
</html>
"""


@dataclass
class ServerSettings:
    db_path: str = DEFAULT_DB_PATH
    private_key_hex: str = ""
    admin_token: str = ""
    token_days: int = DEFAULT_TOKEN_DAYS
    require_persistent_db: bool = False
    persistent_root: str = DEFAULT_PERSISTENT_ROOT


def load_settings() -> ServerSettings:
    return ServerSettings(
        db_path=os.environ.get("PORTALCALC_LICENSE_DB", DEFAULT_DB_PATH),
        private_key_hex=os.environ.get("PORTALCALC_LICENSE_PRIVATE_KEY", ""),
        admin_token=os.environ.get("PORTALCALC_LICENSE_ADMIN_TOKEN", ""),
        token_days=int(os.environ.get("PORTALCALC_LICENSE_TOKEN_DAYS", str(DEFAULT_TOKEN_DAYS))),
        require_persistent_db=os.environ.get("PORTALCALC_REQUIRE_PERSISTENT_DB", "").lower() in {"1", "true", "yes"},
        persistent_root=os.environ.get("PORTALCALC_PERSISTENT_ROOT", DEFAULT_PERSISTENT_ROOT),
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


def backup_path(db_path: str) -> Path:
    return Path(f"{db_path}.backup")


def validate_database_location(settings: ServerSettings) -> None:
    if not settings.require_persistent_db:
        return
    db_path = Path(settings.db_path).resolve()
    persistent_root = Path(settings.persistent_root).resolve()
    if not persistent_root.is_dir():
        raise RuntimeError(f"Persistent disk is not mounted at {persistent_root}.")
    if db_path == persistent_root or persistent_root not in db_path.parents:
        raise RuntimeError(
            f"Refusing to use ephemeral database path {db_path}; it must be inside {persistent_root}."
        )


def connect(db_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def backup_database(db_path: str) -> None:
    destination_path = backup_path(db_path)
    temporary_path = Path(f"{destination_path}.tmp")
    with _BACKUP_LOCK:
        source = sqlite3.connect(db_path)
        destination = sqlite3.connect(temporary_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        os.replace(temporary_path, destination_path)


def restore_database_backup(db_path: str) -> bool:
    source_path = backup_path(db_path)
    if Path(db_path).exists() or not source_path.exists():
        return False
    temporary_path = Path(f"{db_path}.restore")
    with _BACKUP_LOCK:
        source = sqlite3.connect(source_path)
        destination = sqlite3.connect(temporary_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        os.replace(temporary_path, db_path)
        return True


@contextmanager
def db_session(db_path: str, backup_on_commit: bool = False):
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()
    if backup_on_commit:
        backup_database(db_path)


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
                machine_name TEXT,
                activated_at INTEGER NOT NULL,
                last_seen_at INTEGER NOT NULL,
                PRIMARY KEY (license_key_hash, machine_id),
                FOREIGN KEY (license_key_hash) REFERENCES licenses (license_key_hash)
            );
            """
        )
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(activations)").fetchall()
        }
        if "machine_name" not in columns:
            db.execute("ALTER TABLE activations ADD COLUMN machine_name TEXT")


def create_license(
    db_path: str,
    customer: str,
    plan: str,
    expires_at: int | None,
    max_machines: int = 1,
    license_key: str | None = None,
) -> str:
    license_key = license_key or make_license_key()
    with db_session(db_path, backup_on_commit=True) as db:
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


def license_summary(row: sqlite3.Row, activations: int) -> dict[str, Any]:
    license_key_hash = str(row["license_key_hash"])
    return {
        "license_key_hash": license_key_hash,
        "license_key_hash_short": license_key_hash[:12],
        "customer": row["customer"],
        "plan": row["plan"],
        "expires_at": row["expires_at"],
        "max_machines": row["max_machines"],
        "status": row["status"],
        "created_at": row["created_at"],
        "activations": activations,
    }


def list_licenses(db_path: str) -> list[dict[str, Any]]:
    with db_session(db_path) as db:
        rows = db.execute(
            """
            SELECT l.*, COUNT(a.machine_id) AS activation_count
            FROM licenses l
            LEFT JOIN activations a ON a.license_key_hash = l.license_key_hash
            GROUP BY l.license_key_hash
            ORDER BY l.created_at DESC
            """
        ).fetchall()
        return [license_summary(row, int(row["activation_count"])) for row in rows]


def license_detail(db_path: str, license_key_hash: str) -> dict[str, Any] | None:
    with db_session(db_path) as db:
        row = license_row(db, license_key_hash)
        if row is None:
            return None
        activations = db.execute(
            """
            SELECT machine_id, machine_name, activated_at, last_seen_at
            FROM activations
            WHERE license_key_hash = ?
            ORDER BY last_seen_at DESC
            """,
            (license_key_hash,),
        ).fetchall()
        detail = license_summary(row, len(activations))
        detail["activation_rows"] = [dict(activation) for activation in activations]
        return detail


def set_license_status(db_path: str, license_key_hash: str, status: str) -> bool:
    with db_session(db_path, backup_on_commit=True) as db:
        result = db.execute(
            "UPDATE licenses SET status = ? WHERE license_key_hash = ?",
            (status, license_key_hash),
        )
        return result.rowcount > 0


def update_license(
    db_path: str,
    license_key_hash: str,
    customer: str,
    plan: str,
    expires_at: int | None,
    max_machines: int,
) -> bool:
    with db_session(db_path, backup_on_commit=True) as db:
        result = db.execute(
            """
            UPDATE licenses
            SET customer = ?, plan = ?, expires_at = ?, max_machines = ?
            WHERE license_key_hash = ?
            """,
            (customer, plan, expires_at, max_machines, license_key_hash),
        )
        return result.rowcount > 0


def delete_activation(db_path: str, license_key_hash: str, machine_id: str) -> bool:
    with db_session(db_path, backup_on_commit=True) as db:
        result = db.execute(
            "DELETE FROM activations WHERE license_key_hash = ? AND machine_id = ?",
            (license_key_hash, machine_id),
        )
        return result.rowcount > 0


def build_token_payload(row: sqlite3.Row, license_key_hash: str, machine_id: str) -> dict[str, Any]:
    return {
        "license_key_hash": license_key_hash,
        "machine_id": machine_id,
        "customer": row["customer"],
        "plan": row["plan"],
        "expires_at": row["expires_at"],
        "issued_at": utc_now(),
    }


def normalized_machine_name(machine_name: str | None) -> str:
    return str(machine_name or "").strip()[:120]


def activate_license(settings: ServerSettings, license_key: str, machine_id: str, machine_name: str | None = None, legacy_machine_id: str | None = None) -> dict[str, Any]:
    license_key_hash = hash_license_key(license_key.strip())
    now = utc_now()
    display_name = normalized_machine_name(machine_name)
    with db_session(settings.db_path, backup_on_commit=True) as db:
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
        legacy_existing = None
        if legacy_machine_id and legacy_machine_id != machine_id:
            legacy_existing = db.execute(
                "SELECT 1 FROM activations WHERE license_key_hash = ? AND machine_id = ?",
                (license_key_hash, legacy_machine_id),
            ).fetchone()
        if existing is None and activation_count(db, license_key_hash) >= int(row["max_machines"]):
            if legacy_existing is None:
                return {"ok": False, "message": "License has reached its machine limit."}

        if legacy_existing is not None:
            db.execute(
                "DELETE FROM activations WHERE license_key_hash = ? AND machine_id = ?",
                (license_key_hash, legacy_machine_id),
            )

        db.execute(
            """
            INSERT INTO activations (license_key_hash, machine_id, machine_name, activated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(license_key_hash, machine_id)
            DO UPDATE SET
                machine_name = COALESCE(NULLIF(excluded.machine_name, ''), activations.machine_name),
                last_seen_at = excluded.last_seen_at
            """,
            (license_key_hash, machine_id, display_name, now, now),
        )
        token = sign_payload(build_token_payload(row, license_key_hash, machine_id), settings.private_key_hex)
        return {"ok": True, "token": token}


def check_license(settings: ServerSettings, token: str, machine_id: str, machine_name: str | None = None, legacy_machine_id: str | None = None) -> dict[str, Any]:
    try:
        public_key_hex = public_key_hex_from_private(settings.private_key_hex)
        payload = verify_token(token, public_key_hex)
    except Exception:
        return {"revoked": True, "message": "License token is invalid."}

    license_key_hash = str(payload.get("license_key_hash", ""))
    token_machine_id = str(payload.get("machine_id", ""))
    migration_allowed = bool(legacy_machine_id and token_machine_id == legacy_machine_id)
    if token_machine_id != machine_id and not migration_allowed:
        return {"revoked": True, "message": "License is activated for another machine."}

    now = utc_now()
    display_name = normalized_machine_name(machine_name)
    healed = False
    with db_session(settings.db_path) as db:
        row = license_row(db, license_key_hash)
        if row is None:
            db.execute(
                """
                INSERT INTO licenses (
                    license_key_hash, customer, plan, expires_at, max_machines, status, created_at
                )
                VALUES (?, ?, ?, ?, 1, 'active', ?)
                """,
                (
                    license_key_hash,
                    str(payload.get("customer", "Recovered license")),
                    str(payload.get("plan", "standard") or "standard"),
                    payload.get("expires_at"),
                    now,
                ),
            )
            healed = True
            row = license_row(db, license_key_hash)
        if row is None or row["status"] != "active":
            return {"revoked": True, "message": "License has been revoked."}
        if row["expires_at"] is not None and int(row["expires_at"]) < now:
            return {"revoked": True, "message": "License has expired."}
        existing = db.execute(
            "SELECT 1 FROM activations WHERE license_key_hash = ? AND machine_id = ?",
            (license_key_hash, machine_id),
        ).fetchone()
        if existing is None and migration_allowed:
            legacy_existing = db.execute(
                "SELECT 1 FROM activations WHERE license_key_hash = ? AND machine_id = ?",
                (license_key_hash, token_machine_id),
            ).fetchone()
            if legacy_existing is not None:
                db.execute(
                    "DELETE FROM activations WHERE license_key_hash = ? AND machine_id = ?",
                    (license_key_hash, token_machine_id),
                )
                existing = legacy_existing
        if existing is None:
            return {"revoked": True, "message": "This machine activation has been removed."}
        db.execute(
            """
            INSERT INTO activations (license_key_hash, machine_id, machine_name, activated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(license_key_hash, machine_id) DO NOTHING
            """,
            (license_key_hash, machine_id, display_name, now, now),
        )
        if display_name:
            db.execute(
                "UPDATE activations SET machine_name = ?, last_seen_at = ? WHERE license_key_hash = ? AND machine_id = ?",
                (display_name, now, license_key_hash, machine_id),
            )
        else:
            db.execute(
                "UPDATE activations SET last_seen_at = ? WHERE license_key_hash = ? AND machine_id = ?",
                (now, license_key_hash, machine_id),
            )
        response = {"revoked": False, "token": sign_payload(build_token_payload(row, license_key_hash, machine_id), settings.private_key_hex)}
    if healed:
        backup_database(settings.db_path)
    return response


def create_app(settings: ServerSettings | None = None):
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.responses import HTMLResponse
        from pydantic import BaseModel
    except ImportError as exc:
        raise RuntimeError("Install fastapi and uvicorn to run the license server.") from exc

    settings = settings or load_settings()
    app = FastAPI(title="PortalCalc License API", version="0.1.0")

    class ActivateRequest(BaseModel):
        license_key: str
        machine_id: str
        machine_name: str | None = None
        legacy_machine_id: str | None = None

    class CheckRequest(BaseModel):
        token: str
        machine_id: str
        machine_name: str | None = None
        legacy_machine_id: str | None = None

    class CreateLicenseRequest(BaseModel):
        customer: str
        plan: str = "standard"
        expires_at: int | None = None
        max_machines: int = 1

    class UpdateLicenseRequest(BaseModel):
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
        validate_database_location(settings)
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        restore_database_backup(settings.db_path)
        init_db(settings.db_path)

    @app.get("/health")
    def health() -> dict[str, Any]:
        with db_session(settings.db_path) as db:
            db.execute("SELECT 1 FROM licenses LIMIT 1").fetchone()
        return {"ok": True, "database": "ok"}

    @app.get("/public-key")
    def public_key() -> dict[str, Any]:
        return {"public_key_hex": public_key_hex_from_private(settings.private_key_hex)}

    @app.post("/activate")
    def activate(request: ActivateRequest) -> dict[str, Any]:
        return activate_license(settings, request.license_key, request.machine_id, request.machine_name, request.legacy_machine_id)

    @app.post("/check")
    def check(request: CheckRequest) -> dict[str, Any]:
        return check_license(settings, request.token, request.machine_id, request.machine_name, request.legacy_machine_id)

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

    @app.get("/admin/licenses")
    def admin_list_licenses(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
        require_admin(x_admin_token)
        return {"licenses": list_licenses(settings.db_path)}

    @app.get("/admin/licenses/{license_key_hash}")
    def admin_license_detail(
        license_key_hash: str,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        detail = license_detail(settings.db_path, license_key_hash)
        if detail is None:
            raise HTTPException(status_code=404, detail="License not found")
        return detail

    @app.patch("/admin/licenses/{license_key_hash}")
    def admin_update_license(
        license_key_hash: str,
        request: UpdateLicenseRequest,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        customer = request.customer.strip()
        plan = request.plan.strip() or "standard"
        max_machines = max(int(request.max_machines), 1)
        if not customer:
            raise HTTPException(status_code=400, detail="Customer is required")
        if not update_license(
            settings.db_path,
            license_key_hash,
            customer=customer,
            plan=plan,
            expires_at=request.expires_at,
            max_machines=max_machines,
        ):
            raise HTTPException(status_code=404, detail="License not found")
        return {"ok": True}

    @app.post("/admin/licenses/{license_key_hash}/cancel")
    def admin_cancel_license(
        license_key_hash: str,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        if not set_license_status(settings.db_path, license_key_hash, "canceled"):
            raise HTTPException(status_code=404, detail="License not found")
        return {"ok": True, "status": "canceled"}

    @app.post("/admin/licenses/{license_key_hash}/reactivate")
    def admin_reactivate_license(
        license_key_hash: str,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        if not set_license_status(settings.db_path, license_key_hash, "active"):
            raise HTTPException(status_code=404, detail="License not found")
        return {"ok": True, "status": "active"}

    @app.delete("/admin/licenses/{license_key_hash}/activations/{machine_id}")
    def admin_delete_activation(
        license_key_hash: str,
        machine_id: str,
        x_admin_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_admin(x_admin_token)
        if not delete_activation(settings.db_path, license_key_hash, machine_id):
            raise HTTPException(status_code=404, detail="Activation not found")
        return {"ok": True}

    @app.get("/admin", response_class=HTMLResponse)
    def admin_view() -> HTMLResponse:
        return HTMLResponse(ADMIN_HTML)

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
