from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class FanCurveEditorWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fan Curve Editor")
        self.resize(520, 360)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.enable_fan = QCheckBox("Enable cooling fan parameter")
        self.enable_fan.setChecked(True)

        self.temp_spins: list[QSpinBox] = []
        self.hyst_spins: list[QSpinBox] = []
        self.speed_spins: list[QSpinBox] = []

        curve_group = QGroupBox("Cooling Levels")
        curve_grid = QGridLayout(curve_group)
        curve_grid.addWidget(QWidget(), 0, 0)

        headers = ["Temp (mC)", "Hyst (mC)", "PWM (0-255)"]
        for column, label in enumerate(headers, start=1):
            header_widget = QPushButton(label)
            header_widget.setEnabled(False)
            curve_grid.addWidget(header_widget, 0, column)

        defaults = [
            (50000, 5000, 75),
            (60000, 5000, 125),
            (67500, 5000, 175),
            (75000, 5000, 250),
        ]

        for row, (temp_default, hyst_default, speed_default) in enumerate(defaults, start=1):
            level_label = QPushButton(f"Level {row}")
            level_label.setEnabled(False)
            curve_grid.addWidget(level_label, row, 0)

            temp_spin = QSpinBox()
            temp_spin.setRange(20000, 120000)
            temp_spin.setSingleStep(500)
            temp_spin.setValue(temp_default)

            hyst_spin = QSpinBox()
            hyst_spin.setRange(1000, 20000)
            hyst_spin.setSingleStep(500)
            hyst_spin.setValue(hyst_default)

            speed_spin = QSpinBox()
            speed_spin.setRange(0, 255)
            speed_spin.setValue(speed_default)

            self.temp_spins.append(temp_spin)
            self.hyst_spins.append(hyst_spin)
            self.speed_spins.append(speed_spin)

            curve_grid.addWidget(temp_spin, row, 1)
            curve_grid.addWidget(hyst_spin, row, 2)
            curve_grid.addWidget(speed_spin, row, 3)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addWidget(self.enable_fan)
        layout.addWidget(curve_group)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []
        if self.enable_fan.isChecked():
            lines.append("dtparam=cooling_fan=on")

        for index in range(4):
            lines.append(f"dtparam=fan_temp{index}={self.temp_spins[index].value()}")
            lines.append(f"dtparam=fan_temp{index}_hyst={self.hyst_spins[index].value()}")
            lines.append(f"dtparam=fan_temp{index}_speed={self.speed_spins[index].value()}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
