from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ActivityLedWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activity LED Behavior")
        self.resize(420, 240)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.trigger_combo = QComboBox()
        self.trigger_combo.addItems(["mmc", "heartbeat", "cpu", "gpio", "none"])

        self.active_low = QCheckBox("Set activity LED active-low")

        self.use_custom_gpio = QCheckBox("Use custom activity LED GPIO")
        self.gpio_spin = QSpinBox()
        self.gpio_spin.setRange(0, 53)
        self.gpio_spin.setValue(16)
        self.gpio_spin.setEnabled(False)
        self.use_custom_gpio.toggled.connect(self.gpio_spin.setEnabled)

        form = QFormLayout()
        form.addRow("Trigger", self.trigger_combo)
        form.addRow("", self.active_low)
        form.addRow("", self.use_custom_gpio)
        form.addRow("GPIO", self.gpio_spin)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        trigger = self.trigger_combo.currentText()
        active_low_value = "on" if self.active_low.isChecked() else "off"

        lines = [
            f"dtparam=act_led_trigger={trigger}",
            f"dtparam=act_led_activelow={active_low_value}",
        ]

        if self.use_custom_gpio.isChecked():
            lines.append(f"dtparam=act_led_gpio={self.gpio_spin.value()}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
