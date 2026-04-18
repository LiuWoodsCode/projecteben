from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QMainWindow,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class AntennaConfigurationWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Antenna Configuration")
        self.resize(420, 220)

        central = QWidget(self)
        self.setCentralWidget(central)

        self.ant1_radio = QRadioButton("Use antenna 1 (default)")
        self.ant2_radio = QRadioButton("Use antenna 2")
        self.no_ant_radio = QRadioButton("Disable both antennas")
        self.ant1_radio.setChecked(True)

        self.no_hogs_check = QCheckBox("Disable GPIO hogs (noanthogs)")

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addWidget(self.ant1_radio)
        layout.addWidget(self.ant2_radio)
        layout.addWidget(self.no_ant_radio)
        layout.addWidget(self.no_hogs_check)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []
        if self.ant2_radio.isChecked():
            lines.append("dtparam=ant2")
        elif self.no_ant_radio.isChecked():
            lines.append("dtparam=noant")
        else:
            lines.append("dtparam=ant1")

        if self.no_hogs_check.isChecked():
            lines.append("dtparam=noanthogs")

        self.snippet_ready.emit("\n".join(lines) + "\n")
