from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BootVideoCoreWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boot: VideoCore")
        self.resize(520, 220)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.start_file_edit = QLineEdit()
        self.start_file_edit.setPlaceholderText("Example: start4.elf")

        self.fixup_file_edit = QLineEdit()
        self.fixup_file_edit.setPlaceholderText("Example: fixup4.dat")

        form = QFormLayout()
        form.addRow("start_file", self.start_file_edit)
        form.addRow("fixup_file", self.fixup_file_edit)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []

        start_file = self.start_file_edit.text().strip()
        fixup_file = self.fixup_file_edit.text().strip()

        if start_file:
            lines.append(f"start_file={start_file}")
        if fixup_file:
            lines.append(f"fixup_file={fixup_file}")

        if lines:
            self.snippet_ready.emit("\n".join(lines) + "\n")
