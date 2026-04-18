from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


MAC_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$|^[0-9A-Fa-f]{12}$")


class BtWlanAddressWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("BT/WLAN Address")
        self.resize(500, 240)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.bdaddr_edit = QLineEdit()
        self.bdaddr_edit.setPlaceholderText("Example: 06:05:04:03:02:01")

        self.wifiaddr_edit = QLineEdit()
        self.wifiaddr_edit.setPlaceholderText("Example: AA:BB:CC:DD:EE:FF")

        self.krnbt_check = QCheckBox("Enable Bluetooth autoprobing (krnbt)")
        self.krnbt_check.setChecked(True)

        form = QFormLayout()
        form.addRow("Bluetooth Address", self.bdaddr_edit)
        form.addRow("WiFi Address", self.wifiaddr_edit)
        form.addRow("", self.krnbt_check)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        bdaddr = self.bdaddr_edit.text().strip()
        wifiaddr = self.wifiaddr_edit.text().strip()

        if bdaddr and not MAC_PATTERN.fullmatch(bdaddr):
            QMessageBox.warning(self, "BT/WLAN Address", "Bluetooth address format is invalid.")
            return

        if wifiaddr and not MAC_PATTERN.fullmatch(wifiaddr):
            QMessageBox.warning(self, "BT/WLAN Address", "WiFi address format is invalid.")
            return

        lines = [f"dtparam=krnbt={'on' if self.krnbt_check.isChecked() else 'off'}"]

        if bdaddr:
            lines.append(f"dtparam=bdaddr={bdaddr}")
        if wifiaddr:
            lines.append(f"dtparam=wifiaddr={wifiaddr}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
