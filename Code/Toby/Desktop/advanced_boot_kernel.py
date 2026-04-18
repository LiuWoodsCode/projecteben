from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BootKernelWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boot: Kernel")
        self.resize(620, 340)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.cmdline_edit = QLineEdit()
        self.cmdline_edit.setPlaceholderText("Example: cmdline.txt")

        self.kernel_edit = QLineEdit()
        self.kernel_edit.setPlaceholderText("Example: kernel8.img")

        self.armstub_edit = QLineEdit()
        self.armstub_edit.setPlaceholderText("Example: armstub8.bin")

        self.os_prefix_edit = QLineEdit()
        self.os_prefix_edit.setPlaceholderText("Example: test/ or test-")

        self.overlay_prefix_edit = QLineEdit()
        self.overlay_prefix_edit.setPlaceholderText("Example: overlays/")

        self.force_64bit_check = QCheckBox("Force 64-bit kernel mode (arm_64bit=1)")
        self.force_64bit_check.setChecked(True)

        form = QFormLayout()
        form.addRow("cmdline", self.cmdline_edit)
        form.addRow("kernel", self.kernel_edit)
        form.addRow("armstub", self.armstub_edit)
        form.addRow("os_prefix", self.os_prefix_edit)
        form.addRow("overlay_prefix", self.overlay_prefix_edit)
        form.addRow("", self.force_64bit_check)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []

        cmdline = self.cmdline_edit.text().strip()
        kernel = self.kernel_edit.text().strip()
        armstub = self.armstub_edit.text().strip()
        os_prefix = self.os_prefix_edit.text().strip()
        overlay_prefix = self.overlay_prefix_edit.text().strip()

        if cmdline:
            lines.append(f"cmdline={cmdline}")
        if kernel:
            lines.append(f"kernel={kernel}")
        lines.append(f"arm_64bit={1 if self.force_64bit_check.isChecked() else 0}")
        if armstub:
            lines.append(f"armstub={armstub}")
        if os_prefix:
            lines.append(f"os_prefix={os_prefix}")
        if overlay_prefix:
            lines.append(f"overlay_prefix={overlay_prefix}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
