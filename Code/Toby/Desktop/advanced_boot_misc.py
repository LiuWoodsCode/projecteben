from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class BootMiscWindow(QMainWindow):
    snippet_ready = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Boot: Misc")
        self.resize(700, 620)

        central = QWidget(self)
        self.setCentralWidget(central)

        toggles_group = QGroupBox("General Boot Toggles")
        toggles_form = QFormLayout(toggles_group)

        self.disable_poe_fan_check = QCheckBox("disable_poe_fan=1")
        self.disable_splash_check = QCheckBox("disable_splash=1")
        self.enable_uart_check = QCheckBox("enable_uart=1")
        self.force_eeprom_read_check = QCheckBox("force_eeprom_read=1")
        self.otg_mode_check = QCheckBox("otg_mode=1 (Pi 4)")

        toggles_form.addRow("", self.disable_poe_fan_check)
        toggles_form.addRow("", self.disable_splash_check)
        toggles_form.addRow("", self.enable_uart_check)
        toggles_form.addRow("", self.force_eeprom_read_check)
        toggles_form.addRow("", self.otg_mode_check)

        bootloader_group = QGroupBox("Bootloader / Recovery")
        bootloader_form = QFormLayout(bootloader_group)

        self.boot_ramdisk_check = QCheckBox("boot_ramdisk=1")
        self.boot_load_flags_spin = QSpinBox()
        self.boot_load_flags_spin.setRange(0, 255)
        self.boot_load_flags_spin.setValue(0)

        self.enable_rp1_uart_check = QCheckBox("enable_rp1_uart=1 (Pi 5)")
        self.pciex4_reset_check = QCheckBox("pciex4_reset=1")
        self.pciex4_reset_check.setChecked(True)

        self.sha256_check = QCheckBox("sha256=1")
        self.uart_2ndstage_check = QCheckBox("uart_2ndstage=1")
        self.erase_eeprom_check = QCheckBox("erase_eeprom=1 (recovery.bin only)")

        self.set_reboot_arg1_edit = QLineEdit()
        self.set_reboot_order_edit = QLineEdit()

        self.eeprom_write_protect_spin = QSpinBox()
        self.eeprom_write_protect_spin.setRange(-1, 1)
        self.eeprom_write_protect_spin.setValue(-1)

        self.os_check_check = QCheckBox("os_check=1")
        self.os_check_check.setChecked(True)

        self.bootloader_update_check = QCheckBox("bootloader_update=1")
        self.bootloader_update_check.setChecked(True)

        bootloader_form.addRow("", self.boot_ramdisk_check)
        bootloader_form.addRow("boot_load_flags", self.boot_load_flags_spin)
        bootloader_form.addRow("", self.enable_rp1_uart_check)
        bootloader_form.addRow("", self.pciex4_reset_check)
        bootloader_form.addRow("", self.sha256_check)
        bootloader_form.addRow("", self.uart_2ndstage_check)
        bootloader_form.addRow("", self.erase_eeprom_check)
        bootloader_form.addRow("set_reboot_arg1", self.set_reboot_arg1_edit)
        bootloader_form.addRow("set_reboot_order", self.set_reboot_order_edit)
        bootloader_form.addRow("eeprom_write_protect", self.eeprom_write_protect_spin)
        bootloader_form.addRow("", self.os_check_check)
        bootloader_form.addRow("", self.bootloader_update_check)

        watchdog_group = QGroupBox("Kernel Watchdog")
        watchdog_form = QFormLayout(watchdog_group)

        self.kernel_watchdog_timeout_spin = QSpinBox()
        self.kernel_watchdog_timeout_spin.setRange(0, 3600)
        self.kernel_watchdog_timeout_spin.setValue(0)

        self.kernel_watchdog_partition_spin = QSpinBox()
        self.kernel_watchdog_partition_spin.setRange(0, 255)
        self.kernel_watchdog_partition_spin.setValue(0)

        watchdog_form.addRow("kernel_watchdog_timeout", self.kernel_watchdog_timeout_spin)
        watchdog_form.addRow("kernel_watchdog_partition", self.kernel_watchdog_partition_spin)

        insert_button = QPushButton("Insert Settings")
        insert_button.clicked.connect(self.emit_snippet)

        layout = QVBoxLayout(central)
        layout.addWidget(toggles_group)
        layout.addWidget(bootloader_group)
        layout.addWidget(watchdog_group)
        layout.addStretch(1)
        layout.addWidget(insert_button)

    def emit_snippet(self) -> None:
        lines: list[str] = []

        if self.disable_poe_fan_check.isChecked():
            lines.append("disable_poe_fan=1")
        if self.disable_splash_check.isChecked():
            lines.append("disable_splash=1")
        if self.enable_uart_check.isChecked():
            lines.append("enable_uart=1")
        if self.force_eeprom_read_check.isChecked():
            lines.append("force_eeprom_read=1")
        if self.otg_mode_check.isChecked():
            lines.append("otg_mode=1")

        if self.boot_ramdisk_check.isChecked():
            lines.append("boot_ramdisk=1")

        lines.append(f"boot_load_flags=0x{self.boot_load_flags_spin.value():x}")

        if self.enable_rp1_uart_check.isChecked():
            lines.append("enable_rp1_uart=1")

        lines.append(f"pciex4_reset={1 if self.pciex4_reset_check.isChecked() else 0}")

        if self.sha256_check.isChecked():
            lines.append("sha256=1")
        if self.uart_2ndstage_check.isChecked():
            lines.append("uart_2ndstage=1")
        if self.erase_eeprom_check.isChecked():
            lines.append("erase_eeprom=1")

        reboot_arg1 = self.set_reboot_arg1_edit.text().strip()
        reboot_order = self.set_reboot_order_edit.text().strip()
        if reboot_arg1:
            lines.append(f"set_reboot_arg1={reboot_arg1}")
        if reboot_order:
            lines.append(f"set_reboot_order={reboot_order}")

        lines.append(f"eeprom_write_protect={self.eeprom_write_protect_spin.value()}")
        lines.append(f"os_check={1 if self.os_check_check.isChecked() else 0}")
        lines.append(f"bootloader_update={1 if self.bootloader_update_check.isChecked() else 0}")

        timeout = self.kernel_watchdog_timeout_spin.value()
        partition = self.kernel_watchdog_partition_spin.value()
        if timeout > 0:
            lines.append(f"kernel_watchdog_timeout={timeout}")
        if partition > 0:
            lines.append(f"kernel_watchdog_partition={partition}")

        self.snippet_ready.emit("\n".join(lines) + "\n")
