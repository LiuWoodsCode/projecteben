from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class AudioSettingsWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Audio Settings")
        self.resize(560, 360)

        central = QWidget(self)
        self.setCentralWidget(central)

        analogue_group = QGroupBox("Onboard Analogue Audio (3.5 mm jack)")
        analogue_form = QFormLayout(analogue_group)

        self.enable_onboard_audio = QCheckBox("Enable onboard analogue audio")
        self.enable_onboard_audio.setChecked(True)

        self.pwm_mode_combo = QComboBox()
        self.pwm_mode_combo.addItem("Mode 2 (high quality, default)", 2)
        self.pwm_mode_combo.addItem("Mode 1 (legacy low quality)", 1)

        self.disable_dither_check = QCheckBox("Disable audio dither")
        self.disable_dither_check.setChecked(False)

        self.enable_dither_check = QCheckBox("Force dither for all bit depths")
        self.enable_dither_check.setChecked(False)

        self.sample_bits_spin = QSpinBox()
        self.sample_bits_spin.setRange(8, 16)
        self.sample_bits_spin.setValue(11)

        analogue_form.addRow("", self.enable_onboard_audio)
        analogue_form.addRow("audio_pwm_mode", self.pwm_mode_combo)
        analogue_form.addRow("", self.disable_dither_check)
        analogue_form.addRow("", self.enable_dither_check)
        analogue_form.addRow("pwm_sample_bits", self.sample_bits_spin)

        hdmi_group = QGroupBox("HDMI Audio")
        hdmi_form = QFormLayout(hdmi_group)

        self.disable_hdmi_audio = QCheckBox("Disable HDMI audio using vc4 overlay suffix")
        self.disable_hdmi_audio.setChecked(False)
        hdmi_form.addRow("", self.disable_hdmi_audio)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addWidget(analogue_group)
        layout.addWidget(hdmi_group)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []

        lines.append(f"dtparam=audio={'on' if self.enable_onboard_audio.isChecked() else 'off'}")
        lines.append(f"audio_pwm_mode={self.pwm_mode_combo.currentData()}")
        lines.append(f"disable_audio_dither={1 if self.disable_dither_check.isChecked() else 0}")
        lines.append(f"enable_audio_dither={1 if self.enable_dither_check.isChecked() else 0}")
        lines.append(f"pwm_sample_bits={self.sample_bits_spin.value()}")

        if self.disable_hdmi_audio.isChecked():
            lines.append("dtoverlay=vc4-kms-v3d,noaudio")

        self.snippet_ready.emit("\n".join(lines) + "\n")
