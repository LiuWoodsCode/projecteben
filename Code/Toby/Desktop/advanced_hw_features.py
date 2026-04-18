from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class HardwareFeaturesWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enable/Disable HW Features")
        self.resize(560, 320)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.feature_map: list[tuple[str, QCheckBox]] = []
        options = [
            ("audio", "Enable onboard audio", False),
            ("i2c_arm", "Enable I2C (ARM)", False),
            ("spi", "Enable SPI", False),
            ("i2s", "Enable I2S", False),
            ("uart0", "Enable UART0", True),
            ("pciex1", "Enable external PCIe", False),
            ("watchdog", "Enable watchdog", True),
            ("random", "Enable hardware RNG", True),
            ("hdmi", "Enable HDMI", True),
            ("rtc", "Enable onboard RTC", True),
        ]

        features_group = QGroupBox("Hardware Features")
        features_grid = QGridLayout(features_group)

        for index, (key, label, checked) in enumerate(options):
            checkbox = QCheckBox(label)
            checkbox.setChecked(checked)
            row = index // 2
            column = index % 2
            features_grid.addWidget(checkbox, row, column)
            self.feature_map.append((key, checkbox))

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addWidget(features_group)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines = []
        for key, checkbox in self.feature_map:
            value = "on" if checkbox.isChecked() else "off"
            lines.append(f"dtparam={key}={value}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
