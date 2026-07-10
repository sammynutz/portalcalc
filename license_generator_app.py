import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from licensing import _ssl_context

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


DEFAULT_LICENSE_API = "https://portalcalc.onrender.com"
APP_ICON_FILENAME = os.path.join("assets", "portalcalc_icon.ico")


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def app_icon() -> QIcon:
    icon = QIcon(resource_path(APP_ICON_FILENAME))
    if icon.isNull():
        icon = QIcon(resource_path(os.path.join("assets", "portalcalc_icon.svg")))
    return icon


def json_request(
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    method: str = "GET",
    timeout: float = 20.0,
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def json_post(url: str, payload: dict, headers: dict | None = None, timeout: float = 20.0) -> dict:
    return json_request(url, payload, headers, "POST", timeout)


def json_get(url: str, headers: dict | None = None, timeout: float = 20.0) -> dict:
    return json_request(url, None, headers, "GET", timeout)


def json_delete(url: str, headers: dict | None = None, timeout: float = 20.0) -> dict:
    return json_request(url, None, headers, "DELETE", timeout)


def json_patch(url: str, payload: dict, headers: dict | None = None, timeout: float = 20.0) -> dict:
    return json_request(url, payload, headers, "PATCH", timeout)


def expiry_timestamp(date_text: str) -> int | None:
    text = date_text.strip()
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())
    except ValueError as exc:
        raise ValueError("Expiry must be blank or YYYY-MM-DD.") from exc


class LicenseGeneratorApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.setWindowTitle("PortalCalc License Admin")
        self.setMinimumWidth(900)

        layout = QVBoxLayout()
        self.setLayout(layout)

        form = QFormLayout()
        layout.addLayout(form)

        self.api_input = QLineEdit(DEFAULT_LICENSE_API)
        self.admin_token_input = QLineEdit()
        self.admin_token_input.setEchoMode(QLineEdit.Password)
        self.show_token_checkbox = QCheckBox("Show")
        self.show_token_checkbox.toggled.connect(self.toggle_token_visibility)
        token_row = QWidget()
        token_layout = QHBoxLayout()
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_row.setLayout(token_layout)
        token_layout.addWidget(self.admin_token_input, 1)
        token_layout.addWidget(self.show_token_checkbox)

        self.customer_input = QLineEdit()
        self.plan_combo = QComboBox()
        self.plan_combo.addItems(["standard", "trial", "business"])
        self.max_machines_input = QSpinBox()
        self.max_machines_input.setRange(1, 999)
        self.max_machines_input.setValue(1)
        self.expiry_input = QLineEdit()
        self.expiry_input.setPlaceholderText("Optional, e.g. 2027-06-30")

        form.addRow("License API", self.api_input)
        form.addRow("Admin token", token_row)
        form.addRow("Customer", self.customer_input)
        form.addRow("Plan", self.plan_combo)
        form.addRow("Max machines", self.max_machines_input)
        form.addRow("Expiry date", self.expiry_input)

        button_row = QHBoxLayout()
        layout.addLayout(button_row)
        self.generate_button = QPushButton("Generate License")
        self.copy_button = QPushButton("Copy Key")
        self.refresh_button = QPushButton("Refresh List")
        self.view_button = QPushButton("View Activations")
        self.edit_button = QPushButton("Edit License")
        self.cancel_button = QPushButton("Cancel")
        self.reactivate_button = QPushButton("Reactivate")
        self.remove_activation_button = QPushButton("Remove Activation")
        self.open_admin_button = QPushButton("Open Web Admin")
        self.copy_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_license)
        self.copy_button.clicked.connect(self.copy_license_key)
        self.refresh_button.clicked.connect(self.refresh_licenses)
        self.view_button.clicked.connect(self.view_selected_license)
        self.edit_button.clicked.connect(self.edit_selected_license)
        self.cancel_button.clicked.connect(self.cancel_selected_license)
        self.reactivate_button.clicked.connect(self.reactivate_selected_license)
        self.remove_activation_button.clicked.connect(self.remove_selected_activation)
        self.open_admin_button.clicked.connect(self.open_web_admin)
        button_row.addWidget(self.generate_button)
        button_row.addWidget(self.copy_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.view_button)
        button_row.addWidget(self.edit_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.reactivate_button)
        button_row.addWidget(self.remove_activation_button)
        button_row.addWidget(self.open_admin_button)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.licenses_table = QTableWidget(0, 7)
        self.licenses_table.setHorizontalHeaderLabels(
            ["Customer", "Plan", "Status", "Machines", "Expires", "Created", "Hash"]
        )
        self.licenses_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.licenses_table.setSelectionMode(QTableWidget.SingleSelection)
        self.licenses_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.licenses_table.itemSelectionChanged.connect(self.update_action_state)
        self.licenses_table.itemDoubleClicked.connect(lambda _item: self.view_selected_license())
        self.licenses_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 7):
            self.licenses_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.licenses_table)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(170)
        layout.addWidget(self.output)

        self.license_key = ""
        self.license_rows: list[dict] = []
        self.current_detail: dict | None = None
        self.update_action_state()

    def toggle_token_visibility(self, checked: bool) -> None:
        self.admin_token_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def set_status(self, text: str, ok: bool = True) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {'#1b7f3a' if ok else '#b00020'}; font-weight: bold;")

    def api_url(self) -> str:
        return self.api_input.text().strip().rstrip("/")

    def admin_headers(self) -> dict:
        return {"X-Admin-Token": self.admin_token_input.text().strip()}

    def require_admin_inputs(self) -> tuple[str, str] | None:
        api_url = self.api_url()
        token = self.admin_token_input.text().strip()
        if not api_url:
            QMessageBox.warning(self, "Missing API", "Enter the license API URL.")
            return None
        if not token:
            QMessageBox.warning(self, "Missing admin token", "Enter the Render admin token.")
            return None
        return api_url, token

    def selected_license(self) -> dict | None:
        row = self.licenses_table.currentRow()
        if row < 0 or row >= len(self.license_rows):
            return None
        return self.license_rows[row]

    def selected_license_hash(self) -> str | None:
        selected = self.selected_license()
        return str(selected.get("license_key_hash", "")) if selected else None

    def update_action_state(self) -> None:
        selected = self.selected_license()
        has_selection = selected is not None
        status = str(selected.get("status", "")) if selected else ""
        self.view_button.setEnabled(has_selection)
        self.edit_button.setEnabled(has_selection)
        self.cancel_button.setEnabled(has_selection and status == "active")
        self.reactivate_button.setEnabled(has_selection and status != "active")
        self.remove_activation_button.setEnabled(bool(self.current_detail and self.current_detail.get("activation_rows")))

    def format_timestamp(self, value: int | None) -> str:
        if not value:
            return "Never"
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def generate_license(self) -> None:
        admin_inputs = self.require_admin_inputs()
        if admin_inputs is None:
            return
        api_url, token = admin_inputs
        customer = self.customer_input.text().strip()
        if not customer:
            QMessageBox.warning(self, "Missing customer", "Enter a customer name.")
            return
        try:
            expires_at = expiry_timestamp(self.expiry_input.text())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid expiry", str(exc))
            return

        payload = {
            "customer": customer,
            "plan": self.plan_combo.currentText(),
            "max_machines": self.max_machines_input.value(),
            "expires_at": expires_at,
        }
        self.generate_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            response = json_post(
                f"{api_url}/admin/licenses",
                payload,
                headers={"X-Admin-Token": token},
            )
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.generate_button.setEnabled(True)

        self.license_key = str(response.get("license_key", ""))
        if not self.license_key:
            self.set_status(f"Failed: {response}", ok=False)
            self.copy_button.setEnabled(False)
            return

        created = time.strftime("%Y-%m-%d %H:%M")
        self.output.setPlainText(
            "\n".join(
                [
                    f"License key: {self.license_key}",
                    f"Customer: {customer}",
                    f"Plan: {payload['plan']}",
                    f"Max machines: {payload['max_machines']}",
                    f"Expiry: {self.expiry_input.text().strip() or 'None'}",
                    f"Created: {created}",
                ]
            )
        )
        self.copy_button.setEnabled(True)
        self.copy_license_key()
        self.set_status("License generated and copied to clipboard.")
        self.refresh_licenses()

    def copy_license_key(self) -> None:
        if self.license_key:
            QApplication.clipboard().setText(self.license_key)

    def refresh_licenses(self) -> None:
        admin_inputs = self.require_admin_inputs()
        if admin_inputs is None:
            return
        api_url, _token = admin_inputs
        self.refresh_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            response = json_get(f"{api_url}/admin/licenses", headers=self.admin_headers())
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh_button.setEnabled(True)

        self.license_rows = list(response.get("licenses", []))
        self.current_detail = None
        self.licenses_table.setRowCount(len(self.license_rows))
        for row_index, license_row in enumerate(self.license_rows):
            values = [
                license_row.get("customer", ""),
                license_row.get("plan", ""),
                license_row.get("status", ""),
                f"{license_row.get('activations', 0)} / {license_row.get('max_machines', 0)}",
                self.format_timestamp(license_row.get("expires_at")),
                self.format_timestamp(license_row.get("created_at")),
                license_row.get("license_key_hash_short", ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in {2, 3, 4, 5, 6}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.licenses_table.setItem(row_index, column, item)
        self.output.setPlainText("")
        self.set_status(f"Loaded {len(self.license_rows)} license(s).")
        self.update_action_state()

    def view_selected_license(self) -> None:
        admin_inputs = self.require_admin_inputs()
        license_hash = self.selected_license_hash()
        if admin_inputs is None or not license_hash:
            return
        api_url, _token = admin_inputs
        try:
            detail = json_get(f"{api_url}/admin/licenses/{license_hash}", headers=self.admin_headers())
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        self.current_detail = detail
        lines = [
            f"Customer: {detail.get('customer', '')}",
            f"Plan: {detail.get('plan', '')}",
            f"Status: {detail.get('status', '')}",
            f"Machines: {detail.get('activations', 0)} / {detail.get('max_machines', 0)}",
            f"Expires: {self.format_timestamp(detail.get('expires_at'))}",
            f"Created: {self.format_timestamp(detail.get('created_at'))}",
            f"Hash: {detail.get('license_key_hash', '')}",
            "",
            "Activations:",
        ]
        activations = detail.get("activation_rows") or []
        if not activations:
            lines.append("  None")
        for index, activation in enumerate(activations, start=1):
            machine_name = str(activation.get("machine_name") or "Unnamed computer")
            machine_id = str(activation.get("machine_id", ""))
            lines.extend(
                [
                    f"  {index}. Machine: {machine_name}",
                    f"     ID: {machine_id}",
                    f"     Activated: {self.format_timestamp(activation.get('activated_at'))}",
                    f"     Last seen: {self.format_timestamp(activation.get('last_seen_at'))}",
                ]
            )
        self.output.setPlainText("\n".join(lines))
        self.set_status("License details loaded.")
        self.update_action_state()

    def edit_selected_license(self) -> None:
        admin_inputs = self.require_admin_inputs()
        selected = self.selected_license()
        license_hash = self.selected_license_hash()
        if admin_inputs is None or selected is None or not license_hash:
            return

        customer, accepted = QInputDialog.getText(
            self,
            "Edit license",
            "Customer",
            text=str(selected.get("customer", "")),
        )
        if not accepted:
            return
        customer = customer.strip()
        if not customer:
            QMessageBox.warning(self, "Missing customer", "Customer is required.")
            return

        plans = ["standard", "trial", "business"]
        current_plan = str(selected.get("plan", "standard") or "standard")
        if current_plan not in plans:
            plans.insert(0, current_plan)
        plan, accepted = QInputDialog.getItem(
            self,
            "Edit license",
            "Plan",
            plans,
            plans.index(current_plan),
            True,
        )
        if not accepted:
            return

        max_machines, accepted = QInputDialog.getInt(
            self,
            "Edit license",
            "Max machines",
            int(selected.get("max_machines", 1) or 1),
            1,
            999,
        )
        if not accepted:
            return

        expires_at = selected.get("expires_at")
        expiry_text = (
            datetime.fromtimestamp(int(expires_at), tz=timezone.utc).strftime("%Y-%m-%d")
            if expires_at
            else ""
        )
        expiry_text, accepted = QInputDialog.getText(
            self,
            "Edit license",
            "Expiry date (blank or YYYY-MM-DD)",
            text=expiry_text,
        )
        if not accepted:
            return
        try:
            parsed_expiry = expiry_timestamp(expiry_text)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid expiry", str(exc))
            return

        api_url, _token = admin_inputs
        payload = {
            "customer": customer,
            "plan": str(plan).strip() or "standard",
            "max_machines": max_machines,
            "expires_at": parsed_expiry,
        }
        try:
            json_patch(
                f"{api_url}/admin/licenses/{license_hash}",
                payload,
                headers=self.admin_headers(),
            )
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        self.set_status("License updated.")
        self.refresh_licenses()

    def set_selected_license_status(self, action: str) -> None:
        admin_inputs = self.require_admin_inputs()
        license_hash = self.selected_license_hash()
        if admin_inputs is None or not license_hash:
            return
        if action == "cancel":
            confirmed = QMessageBox.question(
                self,
                "Cancel license",
                "Cancel this license? The desktop app will fail on the next server check.",
            )
            if confirmed != QMessageBox.Yes:
                return
        api_url, _token = admin_inputs
        try:
            json_post(f"{api_url}/admin/licenses/{license_hash}/{action}", {}, headers=self.admin_headers())
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        self.set_status("License canceled." if action == "cancel" else "License reactivated.")
        self.refresh_licenses()

    def cancel_selected_license(self) -> None:
        self.set_selected_license_status("cancel")

    def reactivate_selected_license(self) -> None:
        self.set_selected_license_status("reactivate")

    def remove_selected_activation(self) -> None:
        admin_inputs = self.require_admin_inputs()
        if admin_inputs is None or not self.current_detail:
            return
        activations = self.current_detail.get("activation_rows") or []
        if not activations:
            return
        machine_id = str(activations[0].get("machine_id", ""))
        if len(activations) > 1:
            activation_labels = [
                f"{item.get('machine_name') or 'Unnamed computer'} - {item.get('machine_id', '')}"
                for item in activations
            ]
            selected_label, accepted = QInputDialog.getItem(
                self,
                "Remove activation",
                "Machine",
                activation_labels,
                0,
                False,
            )
            if not accepted:
                return
            selected_index = activation_labels.index(selected_label)
            machine_id = str(activations[selected_index].get("machine_id", ""))
        confirmed = QMessageBox.question(
            self,
            "Remove activation",
            "Remove this machine activation? The customer can activate another machine if capacity allows.",
        )
        if confirmed != QMessageBox.Yes:
            return
        api_url, _token = admin_inputs
        license_hash = str(self.current_detail.get("license_key_hash", ""))
        try:
            json_delete(
                f"{api_url}/admin/licenses/{license_hash}/activations/{machine_id}",
                headers=self.admin_headers(),
            )
        except Exception as exc:
            self.set_status(f"Failed: {exc}", ok=False)
            return
        self.set_status("Activation removed.")
        self.refresh_licenses()

    def open_web_admin(self) -> None:
        api_url = self.api_url()
        if not api_url:
            QMessageBox.warning(self, "Missing API", "Enter the license API URL.")
            return
        QDesktopServices.openUrl(QUrl(f"{api_url}/admin"))


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    window = LicenseGeneratorApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
