#!/usr/bin/env python3
"""
RF Certification Assist
PySide6 application for Raspberry Pi engineering / certification lab workflows.

Purpose:
- Assist certified EMC/RF labs with repeatable radio-state actions.
- Log operator, DUT, test mode, timestamps, and command output.
- Provide safe wrappers around normal Linux radio tools.

Supported actions:
- List radio adapters
- Toggle Wi-Fi/Bluetooth block state via rfkill
- Scan Wi-Fi networks
- Show Wi-Fi regulatory domain
- Set Wi-Fi regulatory domain using iw reg set
- Show Bluetooth controller information
- Power Bluetooth controller on/off
- Run custom approved lab commands with explicit confirmation

Requirements:
    sudo apt update
    sudo apt install python3-pyside6 rfkill iw wireless-tools network-manager bluez

Run:
    python3 rf_cert_assist.py

Some actions may require sudo privileges.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "RF Certification Assist"
DEFAULT_LOG_DIR = Path.home() / "rf-cert-assist-logs"


@dataclass
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str


class CommandRunner:
    """
    Synchronous command runner for short lab-control commands.

    Intentionally does not use shell=True.
    """

    def run(self, command: list[str], timeout_s: int = 30) -> CommandResult:
        started = dt.datetime.now().isoformat(timespec="seconds")
        printable = " ".join(shlex.quote(part) for part in command)

        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            code = completed.returncode
        except FileNotFoundError as exc:
            stdout = ""
            stderr = f"Executable not found: {exc}"
            code = 127
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\nTimed out after {timeout_s} seconds."
            code = 124

        finished = dt.datetime.now().isoformat(timespec="seconds")
        return CommandResult(printable, code, stdout, stderr, started, finished)


class AuditLogger:
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = self.log_dir / f"rf_cert_audit_{stamp}.csv"
        self.txt_path = self.log_dir / f"rf_cert_session_{stamp}.txt"

        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "started_at",
                    "finished_at",
                    "operator",
                    "dut_id",
                    "test_case",
                    "command",
                    "return_code",
                    "stdout",
                    "stderr",
                    "notes",
                ]
            )

    def log(
        self,
        result: CommandResult,
        operator: str,
        dut_id: str,
        test_case: str,
        notes: str,
    ) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    result.started_at,
                    result.finished_at,
                    operator,
                    dut_id,
                    test_case,
                    result.command,
                    result.return_code,
                    result.stdout,
                    result.stderr,
                    notes,
                ]
            )

        with self.txt_path.open("a", encoding="utf-8") as f:
            f.write("=" * 90 + "\n")
            f.write(f"Started:   {result.started_at}\n")
            f.write(f"Finished:  {result.finished_at}\n")
            f.write(f"Operator:  {operator}\n")
            f.write(f"DUT ID:    {dut_id}\n")
            f.write(f"Test Case: {test_case}\n")
            f.write(f"Command:   {result.command}\n")
            f.write(f"Exit Code: {result.return_code}\n")
            f.write(f"Notes:     {notes}\n\n")
            f.write("--- STDOUT ---\n")
            f.write(result.stdout or "<empty>\n")
            f.write("\n--- STDERR ---\n")
            f.write(result.stderr or "<empty>\n")
            f.write("\n")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.runner = CommandRunner()
        self.logger = AuditLogger(DEFAULT_LOG_DIR)

        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)

        self.operator_edit = QLineEdit()
        self.dut_edit = QLineEdit()
        self.test_case_edit = QLineEdit()
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Lab notes, cable setup, antenna path, chamber state, attenuator values, etc.")
        self.notes_edit.setMaximumHeight(100)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("monospace", 10))

        self.require_confirm = QCheckBox("Require confirmation for RF-affecting actions")
        self.require_confirm.setChecked(True)

        self.country_combo = QComboBox()
        self.country_combo.addItems(
            [
                "US",
                "CA",
                "GB",
                "DE",
                "FR",
                "JP",
                "KR",
                "AU",
                "NZ",
                "BR",
                "MX",
                "IN",
                "SG",
                "TW",
                "CN",
            ]
        )

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(3, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" s")

        self.custom_command = QLineEdit()
        self.custom_command.setPlaceholderText("Example: iw dev wlan0 info")

        self.build_ui()
        self.build_menu()
        self.statusBar().showMessage(f"Logging to {self.logger.log_dir}")

        QTimer.singleShot(200, self.refresh_radio_state)

    def build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_logs = QAction("Open log folder path", self)
        open_logs.triggered.connect(self.show_log_location)
        file_menu.addAction(open_logs)

        choose_logs = QAction("Choose new log folder", self)
        choose_logs.triggered.connect(self.choose_log_folder)
        file_menu.addAction(choose_logs)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Lab Safety")

        safety_action = QAction("Show regulatory safety notice", self)
        safety_action.triggered.connect(self.show_safety_notice)
        help_menu.addAction(safety_action)

    def build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)

        header = QLabel(APP_NAME)
        header.setFont(QFont("Arial", 20, QFont.Bold))
        root_layout.addWidget(header)

        meta_box = QGroupBox("Session Metadata")
        meta_layout = QGridLayout(meta_box)

        self.operator_edit.setPlaceholderText("Operator name / initials")
        self.dut_edit.setPlaceholderText("Device serial, asset tag, or sample ID")
        self.test_case_edit.setPlaceholderText("Example: FCC 15.247 BLE pre-scan, Wi-Fi 2.4 GHz verification")

        meta_layout.addWidget(QLabel("Operator"), 0, 0)
        meta_layout.addWidget(self.operator_edit, 0, 1)
        meta_layout.addWidget(QLabel("DUT ID"), 0, 2)
        meta_layout.addWidget(self.dut_edit, 0, 3)

        meta_layout.addWidget(QLabel("Test Case"), 1, 0)
        meta_layout.addWidget(self.test_case_edit, 1, 1, 1, 3)

        meta_layout.addWidget(QLabel("Notes"), 2, 0)
        meta_layout.addWidget(self.notes_edit, 2, 1, 1, 3)

        meta_layout.addWidget(self.require_confirm, 3, 1, 1, 2)
        meta_layout.addWidget(QLabel("Command Timeout"), 3, 3)
        meta_layout.addWidget(self.timeout_spin, 3, 4)

        root_layout.addWidget(meta_box)

        tabs = QTabWidget()
        tabs.addTab(self.build_overview_tab(), "Radio Overview")
        tabs.addTab(self.build_wifi_tab(), "Wi-Fi")
        tabs.addTab(self.build_bluetooth_tab(), "Bluetooth")
        tabs.addTab(self.build_custom_tab(), "Approved Custom Command")

        root_layout.addWidget(tabs)

        output_box = QGroupBox("Command Output / Audit Trail")
        output_layout = QVBoxLayout(output_box)
        output_layout.addWidget(self.output)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear Console")
        clear_btn.clicked.connect(self.output.clear)

        refresh_btn = QPushButton("Refresh Radio State")
        refresh_btn.clicked.connect(self.refresh_radio_state)

        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)

        output_layout.addLayout(btn_row)
        root_layout.addWidget(output_box, stretch=1)

        self.setCentralWidget(root)

    def build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        warning = QLabel(
            "Use only inside an authorized lab setup. This tool records actions; it does not grant regulatory approval."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: bold; color: #8a4b00;")
        layout.addWidget(warning)

        grid = QGridLayout()

        actions = [
            ("List rfkill radios", ["rfkill", "list"]),
            ("Show network interfaces", ["ip", "link", "show"]),
            ("Show Wi-Fi devices", ["iw", "dev"]),
            ("Show kernel / OS", ["uname", "-a"]),
            ("Show USB devices", ["lsusb"]),
            ("Show PCI devices", ["lspci"]),
            ("Show loaded radio modules", ["sh", "-c", "lsmod | grep -Ei 'wifi|wlan|bluetooth|btusb|brcm|cfg80211|mac80211' || true"]),
            ("Show NetworkManager radio state", ["nmcli", "radio", "all"]),
        ]

        for i, (label, cmd) in enumerate(actions):
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, c=cmd: self.execute(c, rf_affecting=False))
            grid.addWidget(btn, i // 2, i % 2)

        layout.addLayout(grid)
        layout.addStretch(1)
        return tab

    def build_wifi_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        reg_box = QGroupBox("Regulatory Domain")
        reg_layout = QHBoxLayout(reg_box)

        show_reg = QPushButton("Show Current Regulatory Domain")
        show_reg.clicked.connect(lambda: self.execute(["iw", "reg", "get"], rf_affecting=False))

        set_reg = QPushButton("Set Regulatory Domain")
        set_reg.clicked.connect(self.set_reg_domain)

        reg_layout.addWidget(QLabel("Country"))
        reg_layout.addWidget(self.country_combo)
        reg_layout.addWidget(show_reg)
        reg_layout.addWidget(set_reg)
        reg_layout.addStretch(1)

        layout.addWidget(reg_box)

        wifi_box = QGroupBox("Wi-Fi Actions")
        wifi_layout = QGridLayout(wifi_box)

        buttons = [
            ("Wi-Fi On via nmcli", ["nmcli", "radio", "wifi", "on"], True),
            ("Wi-Fi Off via nmcli", ["nmcli", "radio", "wifi", "off"], True),
            ("Unblock Wi-Fi via rfkill", ["rfkill", "unblock", "wifi"], True),
            ("Block Wi-Fi via rfkill", ["rfkill", "block", "wifi"], True),
            ("Scan Wi-Fi Networks", ["nmcli", "-f", "SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY", "dev", "wifi", "list"], False),
            ("Show Wi-Fi Interface Info", ["iw", "dev"], False),
            ("Show wlan0 Link", ["iw", "dev", "wlan0", "link"], False),
            ("Show wlan0 Station Dump", ["iw", "dev", "wlan0", "station", "dump"], False),
        ]

        for i, (label, cmd, affecting) in enumerate(buttons):
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, c=cmd, a=affecting: self.execute(c, rf_affecting=a))
            wifi_layout.addWidget(btn, i // 2, i % 2)

        layout.addWidget(wifi_box)
        layout.addStretch(1)
        return tab

    def build_bluetooth_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        bt_box = QGroupBox("Bluetooth Actions")
        bt_layout = QGridLayout(bt_box)

        buttons = [
            ("Bluetooth On via rfkill", ["rfkill", "unblock", "bluetooth"], True),
            ("Bluetooth Off via rfkill", ["rfkill", "block", "bluetooth"], True),
            ("Show Bluetooth Controllers", ["bluetoothctl", "list"], False),
            ("Show Bluetooth Controller Info", ["bluetoothctl", "show"], False),
            ("Power Bluetooth On", ["bluetoothctl", "power", "on"], True),
            ("Power Bluetooth Off", ["bluetoothctl", "power", "off"], True),
            ("Show HCI Devices", ["hciconfig", "-a"], False),
            ("Show Bluetooth Service Status", ["systemctl", "status", "bluetooth", "--no-pager"], False),
        ]

        for i, (label, cmd, affecting) in enumerate(buttons):
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, c=cmd, a=affecting: self.execute(c, rf_affecting=a))
            bt_layout.addWidget(btn, i // 2, i % 2)

        layout.addWidget(bt_box)
        layout.addStretch(1)
        return tab

    def build_custom_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        notice = QLabel(
            "Custom commands are for approved lab procedures only. "
            "They are logged verbatim and require confirmation."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("font-weight: bold;")
        layout.addWidget(notice)

        form = QFormLayout()
        form.addRow("Command", self.custom_command)
        layout.addLayout(form)

        run_btn = QPushButton("Run Approved Custom Command")
        run_btn.clicked.connect(self.run_custom_command)

        layout.addWidget(run_btn)
        layout.addStretch(1)
        return tab

    def current_metadata(self) -> tuple[str, str, str, str]:
        operator = self.operator_edit.text().strip() or "UNSPECIFIED"
        dut_id = self.dut_edit.text().strip() or "UNSPECIFIED"
        test_case = self.test_case_edit.text().strip() or "UNSPECIFIED"
        notes = self.notes_edit.toPlainText().strip()
        return operator, dut_id, test_case, notes

    def confirm_action(self, command: list[str]) -> bool:
        printable = " ".join(shlex.quote(part) for part in command)
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Confirm RF-Affecting Action")
        msg.setText("This action may change radio state.")
        msg.setInformativeText(
            f"Command:\n{printable}\n\n"
            "Confirm this is authorized by the lab procedure, test plan, and local regulatory requirements."
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        return msg.exec() == QMessageBox.Yes

    def execute(self, command: list[str], rf_affecting: bool) -> None:
        if rf_affecting and self.require_confirm.isChecked():
            if not self.confirm_action(command):
                self.append_output("Action cancelled by operator.\n")
                return

        operator, dut_id, test_case, notes = self.current_metadata()

        self.append_output(f"\n$ {' '.join(shlex.quote(part) for part in command)}\n")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.runner.run(command, timeout_s=self.timeout_spin.value())
        finally:
            QApplication.restoreOverrideCursor()

        self.logger.log(result, operator, dut_id, test_case, notes)

        if result.stdout:
            self.append_output(result.stdout)
        if result.stderr:
            self.append_output("\n[stderr]\n" + result.stderr)
        self.append_output(f"\n[exit code: {result.return_code}]\n")

        self.statusBar().showMessage(f"Logged command to {self.logger.csv_path.name}", 5000)

    def append_output(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)

    def set_reg_domain(self) -> None:
        country = self.country_combo.currentText().strip().upper()
        self.execute(["iw", "reg", "set", country], rf_affecting=True)
        self.execute(["iw", "reg", "get"], rf_affecting=False)

    def run_custom_command(self) -> None:
        raw = self.custom_command.text().strip()
        if not raw:
            QMessageBox.information(self, "No Command", "Enter an approved lab command first.")
            return

        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            QMessageBox.critical(self, "Command Parse Error", str(exc))
            return

        if not parts:
            return

        if not self.confirm_action(parts):
            self.append_output("Custom command cancelled by operator.\n")
            return

        self.execute(parts, rf_affecting=False)

    def refresh_radio_state(self) -> None:
        self.execute(["rfkill", "list"], rf_affecting=False)
        self.execute(["nmcli", "radio", "all"], rf_affecting=False)

    def show_log_location(self) -> None:
        QMessageBox.information(
            self,
            "Log Location",
            f"CSV audit log:\n{self.logger.csv_path}\n\nText session log:\n{self.logger.txt_path}",
        )

    def choose_log_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Log Folder", str(self.logger.log_dir))
        if chosen:
            self.logger = AuditLogger(Path(chosen))
            self.statusBar().showMessage(f"Logging to {self.logger.log_dir}", 5000)

    def show_safety_notice(self) -> None:
        QMessageBox.information(
            self,
            "Regulatory Safety Notice",
            (
                "This application is an engineering aid for authorized RF certification work.\n\n"
                "It does not certify a product, alter hardware authorization, or override regional radio rules.\n"
                "Use conducted or chamber setups, calibrated equipment, approved test plans, and qualified lab personnel.\n\n"
                "All RF-affecting actions should match the DUT authorization path, FCC/IC/CE/UKCA/etc. procedure, "
                "and local legal requirements."
            ),
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())