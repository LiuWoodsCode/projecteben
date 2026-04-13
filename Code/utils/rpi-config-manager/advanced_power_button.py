from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class PowerButtonSettingsWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Power Button Settings")
        self.resize(420, 220)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(1, 500)
        self.debounce_spin.setValue(50)
        self.debounce_spin.setSuffix(" ms")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Power Off", "Suspend"])

        form = QFormLayout()
        form.addRow("Button Debounce", self.debounce_spin)
        form.addRow("Button Action", self.mode_combo)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        suspend_value = "on" if self.mode_combo.currentText() == "Suspend" else "off"
        lines = [
            f"dtparam=button_debounce={self.debounce_spin.value()}",
            f"dtparam=suspend={suspend_value}",
        ]
        self.snippet_ready.emit("\n".join(lines) + "\n")
