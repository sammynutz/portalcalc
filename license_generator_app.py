import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


DEFAULT_LICENSE_API = "https://portalcalc.onrender.com"


def json_post(url: str, payload: dict, headers: dict | None = None, timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


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
        self.setWindowTitle("PortalCalc License Generator")
        self.setMinimumWidth(560)

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
        self.copy_button.setEnabled(False)
        self.generate_button.clicked.connect(self.generate_license)
        self.copy_button.clicked.connect(self.copy_license_key)
        button_row.addWidget(self.generate_button)
        button_row.addWidget(self.copy_button)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(110)
        layout.addWidget(self.output)

        self.license_key = ""

    def toggle_token_visibility(self, checked: bool) -> None:
        self.admin_token_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def set_status(self, text: str, ok: bool = True) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {'#1b7f3a' if ok else '#b00020'}; font-weight: bold;")

    def generate_license(self) -> None:
        api_url = self.api_input.text().strip().rstrip("/")
        token = self.admin_token_input.text().strip()
        customer = self.customer_input.text().strip()
        if not api_url:
            QMessageBox.warning(self, "Missing API", "Enter the license API URL.")
            return
        if not token:
            QMessageBox.warning(self, "Missing admin token", "Enter the Render admin token.")
            return
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

    def copy_license_key(self) -> None:
        if self.license_key:
            QApplication.clipboard().setText(self.license_key)


def main() -> None:
    app = QApplication(sys.argv)
    window = LicenseGeneratorApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
