from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class OverclockConfigWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Overclock Config")
        self.resize(460, 260)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.arm_freq_spin = QSpinBox()
        self.arm_freq_spin.setRange(600, 3200)
        self.arm_freq_spin.setValue(2000)
        self.arm_freq_spin.setSuffix(" MHz")

        self.gpu_mem_spin = QSpinBox()
        self.gpu_mem_spin.setRange(16, 1024)
        self.gpu_mem_spin.setValue(128)
        self.gpu_mem_spin.setSuffix(" MB")

        self.over_voltage_spin = QSpinBox()
        self.over_voltage_spin.setRange(-16, 8)
        self.over_voltage_spin.setValue(0)

        self.force_turbo_check = QCheckBox("Enable force_turbo")
        self.force_turbo_check.setChecked(False)

        form = QFormLayout()
        form.addRow("ARM Frequency", self.arm_freq_spin)
        form.addRow("GPU Memory", self.gpu_mem_spin)
        form.addRow("Over Voltage", self.over_voltage_spin)
        form.addRow("", self.force_turbo_check)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines = [
            f"arm_freq={self.arm_freq_spin.value()}",
            f"gpu_mem={self.gpu_mem_spin.value()}",
            f"over_voltage={self.over_voltage_spin.value()}",
            f"force_turbo={1 if self.force_turbo_check.isChecked() else 0}",
        ]
        self.snippet_ready.emit("\n".join(lines) + "\n")
