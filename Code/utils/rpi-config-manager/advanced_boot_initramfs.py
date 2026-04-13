from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BootInitramfsWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boot: Initramfs")
        self.resize(620, 320)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.ramfsfile_edit = QLineEdit()
        self.ramfsfile_edit.setPlaceholderText("Single file or comma-separated files")

        self.ramfsaddr_edit = QLineEdit()
        self.ramfsaddr_edit.setPlaceholderText("Example: 0x00800000")

        self.initramfs_file_edit = QLineEdit()
        self.initramfs_file_edit.setPlaceholderText("Example: initramfs8")

        self.initramfs_addr_combo = QComboBox()
        self.initramfs_addr_combo.addItems(["followkernel", "0", "custom"])

        self.initramfs_custom_addr = QLineEdit()
        self.initramfs_custom_addr.setPlaceholderText("Example: 0x00800000")
        self.initramfs_custom_addr.setEnabled(False)
        self.initramfs_addr_combo.currentTextChanged.connect(self.on_addr_mode_changed)

        self.auto_initramfs_check = QCheckBox("Enable auto_initramfs")

        form = QFormLayout()
        form.addRow("ramfsfile", self.ramfsfile_edit)
        form.addRow("ramfsaddr", self.ramfsaddr_edit)
        form.addRow("initramfs file", self.initramfs_file_edit)
        form.addRow("initramfs address mode", self.initramfs_addr_combo)
        form.addRow("initramfs custom address", self.initramfs_custom_addr)
        form.addRow("", self.auto_initramfs_check)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def on_addr_mode_changed(self, mode: str) -> None:
        self.initramfs_custom_addr.setEnabled(mode == "custom")

    def emit_snippet(self) -> None:
        lines: list[str] = []

        ramfsfile = self.ramfsfile_edit.text().strip()
        ramfsaddr = self.ramfsaddr_edit.text().strip()
        initramfs_file = self.initramfs_file_edit.text().strip()

        if ramfsfile:
            lines.append(f"ramfsfile={ramfsfile}")
        if ramfsaddr:
            lines.append(f"ramfsaddr={ramfsaddr}")

        if initramfs_file:
            mode = self.initramfs_addr_combo.currentText()
            if mode == "custom":
                addr = self.initramfs_custom_addr.text().strip()
                if not addr:
                    addr = "followkernel"
            else:
                addr = mode
            lines.append(f"initramfs {initramfs_file} {addr}")

        if self.auto_initramfs_check.isChecked():
            lines.append("auto_initramfs=1")

        if lines:
            self.snippet_ready.emit("\n".join(lines) + "\n")
