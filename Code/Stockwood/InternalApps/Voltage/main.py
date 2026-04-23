#!/usr/bin/env python3
"""
Pi Power / PMIC Expert Console
==============================

A Raspberry Pi focused PySide6 engineering utility for power-only inspection.
It deliberately avoids thermal telemetry and broad system inventory. The goal is
not to make a pretty consumer dashboard; it is to expose enough messy electrical
state for debugging supply, PMIC, regulator, USB-C/PD, rail, and undervoltage
behaviour on Raspberry Pi boards.

Tested design targets:
  - Raspberry Pi OS / Linux on Raspberry Pi hardware
  - PySide6
  - Optional tools: vcgencmd, i2c-tools, libgpiod utilities, dmesg access

Install:
  sudo apt update
  sudo apt install -y python3-pyside6.qtwidgets python3-pyside6.qtcore python3-pyside6.qtgui i2c-tools libraspberrypi-bin

Run:
  python3 pi_power_pmic_expert_pyside6.py

Optional permissions:
  - Run as root for I2C register dumps, kernel log power events, and some sysfs nodes.
  - Enable I2C with raspi-config for PMIC/I2C probing.

Safety:
  - Default mode is read-only.
  - The optional I2C tab performs byte reads only.
  - No regulator, GPIO, PMIC, or display power state is changed by this program.

Notes:
  - Raspberry Pi PMIC implementations vary across models and revisions.
  - Not every board exposes a readable PMIC over the ARM-visible I2C buses.
  - Several readings are best-effort interpretations of Linux sysfs and firmware state.
  - This program intentionally does not collect temperature, CPU frequency, RAM,
    OS version, hostname, or general system info.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont, QColor, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Pi Power / PMIC Expert Console"
APP_VERSION = "1.0.0"

POWER_EVENT_PATTERNS = [
    re.compile(r"under[- ]?voltage", re.IGNORECASE),
    re.compile(r"voltage normalised", re.IGNORECASE),
    re.compile(r"throttl", re.IGNORECASE),
    re.compile(r"power", re.IGNORECASE),
    re.compile(r"pmic", re.IGNORECASE),
    re.compile(r"regulator", re.IGNORECASE),
    re.compile(r"vbus", re.IGNORECASE),
    re.compile(r"usb.?c", re.IGNORECASE),
    re.compile(r"type.?c", re.IGNORECASE),
    re.compile(r"pd", re.IGNORECASE),
]

# Common I2C addresses historically seen around Pi power-management silicon or
# companion devices. They are candidates only; a board-specific schematic or
# kernel tree is authoritative.
COMMON_POWER_I2C_ADDRESSES = {
    0x08: "possible PMIC / power controller candidate",
    0x1A: "possible audio/power-adjacent device; verify before trusting",
    0x1B: "possible PMIC candidate on some designs",
    0x2D: "possible regulator / PMIC / monitor candidate",
    0x34: "common AXP-style PMIC address on other SBCs; uncommon on Pi",
    0x36: "common fuel-gauge address; HAT/battery setups",
    0x40: "INA219/INA260-class current monitor candidate",
    0x41: "INA219/INA260-class current monitor candidate",
    0x45: "USB-C/PD or monitor candidate on some accessory designs",
    0x50: "EEPROM/HAT ID; not PMIC but power-HAT relevant",
    0x51: "EEPROM/RTC/fuel-gauge-adjacent candidate",
    0x5A: "USB-C/PD controller candidate on some platforms",
    0x5B: "USB-C/PD controller candidate on some platforms",
    0x60: "regulator/DAC/clock candidate; board dependent",
    0x68: "RTC candidate; battery-backed rail relevance",
    0x69: "RTC/IMU/power accessory candidate",
}

THROTTLED_BITS = {
    0: "UNDER_VOLTAGE_NOW",
    1: "ARM_FREQ_CAPPED_NOW",
    2: "THROTTLED_NOW",
    # Bit 3 is a temperature-related firmware condition; we keep the raw bit
    # visible for correctness but do not present thermal measurements.
    3: "SOFT_LIMIT_NOW_NON_POWER",
    16: "UNDER_VOLTAGE_OCCURRED",
    17: "ARM_FREQ_CAPPED_OCCURRED",
    18: "THROTTLED_OCCURRED",
    19: "SOFT_LIMIT_OCCURRED_NON_POWER",
}

POWER_RELEVANT_VCGENCMD = [
    ["get_throttled"],
    ["measure_volts", "core"],
    ["measure_volts", "sdram_c"],
    ["measure_volts", "sdram_i"],
    ["measure_volts", "sdram_p"],
    ["display_power", "-1"],
]


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read_text(path: Path, max_bytes: int = 1_000_000) -> Optional[str]:
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return None


def read_int(path: Path) -> Optional[int]:
    txt = read_text(path)
    if txt is None or txt == "":
        return None
    try:
        return int(txt, 0)
    except ValueError:
        return None


def run_cmd(args: Sequence[str], timeout: float = 2.0) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 126, "", f"os error: {e}"


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def scale_sysfs_value(name: str, raw: Optional[int]) -> Tuple[Optional[float], str]:
    if raw is None:
        return None, ""
    lname = name.lower()
    if lname.startswith("in") or "voltage" in lname or lname.endswith("_uv"):
        # hwmon usually millivolts for in*_input; regulator sysfs is often microvolts.
        if abs(raw) > 100_000:
            return raw / 1_000_000.0, "V"
        return raw / 1000.0, "V"
    if lname.startswith("curr") or "current" in lname or lname.endswith("_ua"):
        if abs(raw) > 100_000:
            return raw / 1_000_000.0, "A"
        return raw / 1000.0, "A"
    if lname.startswith("power") or lname.endswith("_uw"):
        if abs(raw) > 100_000:
            return raw / 1_000_000.0, "W"
        return raw / 1000.0, "W"
    if lname.startswith("energy"):
        return raw / 1_000_000.0, "J/Wh-like"
    return float(raw), "raw"


def table_item(text: Any, mono: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsEditable)
    if mono:
        item.setFont(QFont("monospace"))
    return item


def classify_quality(value: Optional[float], unit: str, metric_name: str) -> str:
    if value is None:
        return "missing"
    n = metric_name.lower()
    if unit == "V" and ("5v" in n or "vbus" in n or "usb" in n):
        if value < 4.63:
            return "bad: below common 5V undervoltage threshold region"
        if value < 4.85:
            return "marginal"
        return "nominal"
    if unit == "V" and value <= 0:
        return "bad: non-positive voltage"
    if unit == "A" and abs(value) > 10:
        return "suspicious: very high current"
    if unit == "W" and value < 0:
        return "suspicious: negative power"
    return "observed"


@dataclasses.dataclass
class ProbeResult:
    timestamp: str
    vcgencmd: Dict[str, Dict[str, Any]]
    throttled: Dict[str, Any]
    power_supplies: List[Dict[str, Any]]
    regulators: List[Dict[str, Any]]
    hwmon_power: List[Dict[str, Any]]
    usb_power: List[Dict[str, Any]]
    i2c_buses: List[Dict[str, Any]]
    kernel_power_events: List[str]
    debugfs_regulator_summary: Optional[str]
    errors: List[str]


class PowerProbe:
    def __init__(self, include_kernel_log: bool = True, kernel_log_lines: int = 300):
        self.include_kernel_log = include_kernel_log
        self.kernel_log_lines = kernel_log_lines

    def collect(self) -> ProbeResult:
        errors: List[str] = []
        vc = self.collect_vcgencmd(errors)
        throttled = self.decode_throttled(vc)
        power_supplies = self.collect_power_supply(errors)
        regulators = self.collect_regulators(errors)
        hwmon_power = self.collect_hwmon_power(errors)
        usb_power = self.collect_usb_power(errors)
        i2c_buses = self.collect_i2c_buses(errors)
        events = self.collect_kernel_power_events(errors) if self.include_kernel_log else []
        debugfs_summary = self.collect_debugfs_regulator_summary(errors)
        return ProbeResult(
            timestamp=now_iso(),
            vcgencmd=vc,
            throttled=throttled,
            power_supplies=power_supplies,
            regulators=regulators,
            hwmon_power=hwmon_power,
            usb_power=usb_power,
            i2c_buses=i2c_buses,
            kernel_power_events=events,
            debugfs_regulator_summary=debugfs_summary,
            errors=errors,
        )

    def collect_vcgencmd(self, errors: List[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not command_exists("vcgencmd"):
            errors.append("vcgencmd not found; install libraspberrypi-bin or run on Raspberry Pi OS")
            return out
        for cmd in POWER_RELEVANT_VCGENCMD:
            rc, stdout, stderr = run_cmd(["vcgencmd", *cmd], timeout=2.0)
            key = " ".join(cmd)
            out[key] = {"rc": rc, "stdout": stdout, "stderr": stderr}
            if rc != 0:
                errors.append(f"vcgencmd {key!r} failed rc={rc}: {stderr or stdout}")
        return out

    def decode_throttled(self, vc: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        raw = vc.get("get_throttled", {}).get("stdout", "")
        m = re.search(r"0x[0-9a-fA-F]+|\b\d+\b", raw)
        if not m:
            return {"raw": raw, "value": None, "active": [], "occurred": [], "unknown_bits": []}
        value = int(m.group(0), 0)
        active = []
        occurred = []
        known_mask = 0
        for bit, name in THROTTLED_BITS.items():
            known_mask |= 1 << bit
            if value & (1 << bit):
                if bit < 16:
                    active.append(name)
                else:
                    occurred.append(name)
        unknown_bits = [bit for bit in range(32) if value & (1 << bit) and not (known_mask & (1 << bit))]
        return {
            "raw": raw,
            "value": value,
            "hex": f"0x{value:08x}",
            "active": active,
            "occurred": occurred,
            "unknown_bits": unknown_bits,
        }

    def collect_power_supply(self, errors: List[str]) -> List[Dict[str, Any]]:
        root = Path("/sys/class/power_supply")
        rows: List[Dict[str, Any]] = []
        if not root.exists():
            return rows
        for ps in sorted(root.iterdir()):
            if not ps.is_dir():
                continue
            props: Dict[str, Any] = {"name": ps.name, "path": str(ps)}
            for f in sorted(ps.iterdir()):
                if not f.is_file():
                    continue
                key = f.name
                if key in {"uevent"}:
                    continue
                txt = read_text(f, max_bytes=8192)
                if txt is None:
                    continue
                props[key] = txt
            rows.append(props)
        return rows

    def collect_regulators(self, errors: List[str]) -> List[Dict[str, Any]]:
        root = Path("/sys/class/regulator")
        rows: List[Dict[str, Any]] = []
        if not root.exists():
            return rows
        wanted = [
            "name",
            "state",
            "status",
            "type",
            "microvolts",
            "min_microvolts",
            "max_microvolts",
            "microamps",
            "min_microamps",
            "max_microamps",
            "num_users",
            "requested_microamps",
            "opmode",
            "bypass",
            "suspend_state",
            "suspend_microvolts",
            "suspend_mode",
        ]
        for reg in sorted(root.iterdir()):
            if not reg.is_dir():
                continue
            props: Dict[str, Any] = {"id": reg.name, "path": str(reg)}
            for name in wanted:
                txt = read_text(reg / name)
                if txt is not None:
                    props[name] = txt
            rows.append(props)
        return rows

    def collect_hwmon_power(self, errors: List[str]) -> List[Dict[str, Any]]:
        root = Path("/sys/class/hwmon")
        rows: List[Dict[str, Any]] = []
        if not root.exists():
            return rows
        allow_prefixes = ("in", "curr", "power", "energy")
        skip_patterns = ("temp", "fan", "pwm")
        for hw in sorted(root.iterdir()):
            if not hw.is_dir():
                continue
            chip_name = read_text(hw / "name") or hw.name
            attrs: Dict[str, str] = {}
            labels: Dict[str, str] = {}
            for f in sorted(hw.iterdir()):
                if not f.is_file():
                    continue
                fname = f.name
                low = fname.lower()
                if any(p in low for p in skip_patterns):
                    continue
                if not low.startswith(allow_prefixes):
                    continue
                txt = read_text(f)
                if txt is None:
                    continue
                attrs[fname] = txt
                if fname.endswith("_label"):
                    labels[fname.removesuffix("_label")] = txt
            for name, txt in attrs.items():
                if name.endswith("_label"):
                    continue
                raw = None
                try:
                    raw = int(txt, 0)
                except ValueError:
                    pass
                base = name
                base = re.sub(r"_(input|average|lowest|highest|crit|cap|rated_min|rated_max)$", "", base)
                scaled, unit = scale_sysfs_value(name, raw)
                rows.append(
                    {
                        "chip": chip_name,
                        "hwmon": hw.name,
                        "attribute": name,
                        "label": labels.get(base, ""),
                        "raw": txt,
                        "scaled": scaled,
                        "unit": unit,
                        "quality": classify_quality(scaled, unit, f"{chip_name} {name} {labels.get(base, '')}"),
                        "path": str(hw / name),
                    }
                )
        return rows

    def collect_usb_power(self, errors: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        roots = [Path("/sys/bus/usb/devices"), Path("/sys/class/typec")]
        for root in roots:
            if not root.exists():
                continue
            for dev in sorted(root.iterdir()):
                if not dev.is_dir():
                    continue
                props: Dict[str, Any] = {"node": dev.name, "path": str(dev)}
                for key in [
                    "authorized",
                    "bMaxPower",
                    "busnum",
                    "devnum",
                    "devpath",
                    "speed",
                    "manufacturer",
                    "product",
                    "idVendor",
                    "idProduct",
                    "power/control",
                    "power/runtime_status",
                    "power/runtime_active_time",
                    "power/runtime_suspended_time",
                    "data_role",
                    "power_role",
                    "port_type",
                    "preferred_role",
                    "vconn_source",
                    "usb_power_delivery_revision",
                ]:
                    txt = read_text(dev / key)
                    if txt is not None:
                        props[key.replace("/", ".")] = txt
                if len(props) > 2:
                    rows.append(props)
        return rows

    def collect_i2c_buses(self, errors: List[str]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        dev_root = Path("/dev")
        for node in sorted(dev_root.glob("i2c-*")):
            bus = node.name.split("-", 1)[-1]
            meta = {"bus": bus, "device": str(node), "detected": [], "note": ""}
            if command_exists("i2cdetect"):
                rc, stdout, stderr = run_cmd(["i2cdetect", "-y", bus], timeout=4.0)
                meta["scan_rc"] = rc
                meta["scan_stderr"] = stderr
                if rc == 0:
                    found = []
                    for token in re.findall(r"\b[0-9a-fA-F]{2}\b|UU", stdout):
                        if token == "UU":
                            continue
                        try:
                            addr = int(token, 16)
                        except ValueError:
                            continue
                        if 0x03 <= addr <= 0x77:
                            found.append(addr)
                    meta["detected"] = sorted(set(found))
                else:
                    meta["note"] = stderr or stdout
            else:
                meta["note"] = "i2cdetect not installed"
            rows.append(meta)
        return rows

    def collect_kernel_power_events(self, errors: List[str]) -> List[str]:
        if not command_exists("dmesg"):
            return []
        rc, stdout, stderr = run_cmd(["dmesg", "--color=never", "--ctime"], timeout=3.0)
        if rc != 0:
            rc, stdout, stderr = run_cmd(["dmesg"], timeout=3.0)
        if rc != 0:
            errors.append(f"dmesg failed rc={rc}: {stderr or stdout}")
            return []
        lines = stdout.splitlines()[-max(20, self.kernel_log_lines) :]
        selected = []
        for line in lines:
            low = line.lower()
            if "temperature" in low or "thermal" in low:
                continue
            if any(p.search(line) for p in POWER_EVENT_PATTERNS):
                selected.append(line)
        return selected[-self.kernel_log_lines :]

    def collect_debugfs_regulator_summary(self, errors: List[str]) -> Optional[str]:
        p = Path("/sys/kernel/debug/regulator/regulator_summary")
        txt = read_text(p, max_bytes=256_000)
        if txt is not None:
            # Remove nothing power-related; debugfs regulator summary is exactly in scope.
            return txt
        return None


class I2CRegisterDumper:
    def dump(self, bus: int, address: int, start: int, count: int) -> Dict[str, Any]:
        result = {
            "timestamp": now_iso(),
            "bus": bus,
            "address": address,
            "start": start,
            "count": count,
            "values": [],
            "errors": [],
        }
        if not command_exists("i2cget"):
            result["errors"].append("i2cget not installed")
            return result
        for reg in range(start, start + count):
            if reg > 0xFF:
                break
            rc, stdout, stderr = run_cmd(["i2cget", "-y", str(bus), f"0x{address:02x}", f"0x{reg:02x}"], timeout=1.0)
            if rc == 0:
                try:
                    val = int(stdout.strip(), 16)
                except ValueError:
                    val = None
                result["values"].append({"reg": reg, "value": val, "raw": stdout.strip()})
            else:
                result["values"].append({"reg": reg, "value": None, "raw": "ERR"})
                result["errors"].append(f"reg 0x{reg:02x}: rc={rc} {stderr or stdout}")
        return result


class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)


class ProbeWorker(QRunnable):
    def __init__(self, probe: PowerProbe):
        super().__init__()
        self.probe = probe
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.done.emit(self.probe.collect())
        except Exception as e:
            self.signals.error.emit(f"probe crash: {e!r}")


class I2CWorker(QRunnable):
    def __init__(self, bus: int, address: int, start: int, count: int):
        super().__init__()
        self.bus = bus
        self.address = address
        self.start = start
        self.count = count
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.done.emit(I2CRegisterDumper().dump(self.bus, self.address, self.start, self.count))
        except Exception as e:
            self.signals.error.emit(f"i2c dump crash: {e!r}")


class KeyValueTable(QTableWidget):
    def __init__(self, headers: Sequence[str]):
        super().__init__(0, len(headers))
        self.setHorizontalHeaderLabels(list(headers))
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)

    def set_rows(self, rows: Sequence[Sequence[Any]], mono_columns: Iterable[int] = ()):  # type: ignore[type-arg]
        self.setSortingEnabled(False)
        self.setRowCount(0)
        mono = set(mono_columns)
        for row in rows:
            r = self.rowCount()
            self.insertRow(r)
            for c, value in enumerate(row):
                self.setItem(r, c, table_item(value, c in mono))
        self.setSortingEnabled(True)
        self.resizeRowsToContents()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1500, 950)
        self.thread_pool = QThreadPool.globalInstance()
        self.latest: Optional[ProbeResult] = None
        self.history: List[ProbeResult] = []
        self.max_history = 500
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self.refresh)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        toolbar = QToolBar("Power probe controls")
        self.addToolBar(toolbar)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh)
        toolbar.addAction(refresh_action)
        export_action = QAction("Export JSON", self)
        export_action.triggered.connect(self.export_json)
        toolbar.addAction(export_action)
        copy_action = QAction("Copy Snapshot JSON", self)
        copy_action.triggered.connect(self.copy_json)
        toolbar.addAction(copy_action)

        controls = QHBoxLayout()
        self.status_label = QLabel("idle")
        self.auto_check = QCheckBox("auto refresh")
        self.auto_check.stateChanged.connect(self.toggle_auto)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 3600.0)
        self.interval_spin.setValue(3.0)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.valueChanged.connect(self.toggle_auto)
        self.kernel_log_check = QCheckBox("include kernel power events")
        self.kernel_log_check.setChecked(True)
        self.kernel_lines = QSpinBox()
        self.kernel_lines.setRange(20, 5000)
        self.kernel_lines.setValue(300)
        self.busy = QProgressBar()
        self.busy.setRange(0, 1)
        self.busy.setValue(1)
        controls.addWidget(QLabel("sampling:"))
        controls.addWidget(self.auto_check)
        controls.addWidget(self.interval_spin)
        controls.addSpacing(20)
        controls.addWidget(self.kernel_log_check)
        controls.addWidget(QLabel("log lines"))
        controls.addWidget(self.kernel_lines)
        controls.addStretch(1)
        controls.addWidget(self.status_label)
        controls.addWidget(self.busy)
        root.addLayout(controls)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.overview_text = QPlainTextEdit()
        self.overview_text.setReadOnly(True)
        self.overview_text.setFont(QFont("monospace"))
        self.tabs.addTab(self.overview_text, "Power State Summary")

        self.vc_table = KeyValueTable(["command", "rc", "stdout", "stderr", "decoded/notes"])
        self.tabs.addTab(self.vc_table, "Firmware Power / vcgencmd")

        self.reg_table = KeyValueTable([
            "id", "name", "state", "status", "type", "microvolts", "microamps", "users", "opmode", "path"
        ])
        self.tabs.addTab(self.reg_table, "Linux Regulator Framework")

        self.hwmon_table = KeyValueTable([
            "chip", "attribute", "label", "raw", "scaled", "unit", "quality", "path"
        ])
        self.tabs.addTab(self.hwmon_table, "Electrical hwmon")

        self.ps_table = KeyValueTable(["supply", "property", "value", "path"])
        self.tabs.addTab(self.ps_table, "power_supply Class")

        self.usb_table = KeyValueTable(["node", "property", "value", "path"])
        self.tabs.addTab(self.usb_table, "USB / Type-C Power")

        self.i2c_tab = QWidget()
        self.setup_i2c_tab()
        self.tabs.addTab(self.i2c_tab, "I2C PMIC Register Workbench")

        self.debugfs_text = QPlainTextEdit()
        self.debugfs_text.setReadOnly(True)
        self.debugfs_text.setFont(QFont("monospace"))
        self.tabs.addTab(self.debugfs_text, "debugfs regulator_summary")

        self.events_text = QPlainTextEdit()
        self.events_text.setReadOnly(True)
        self.events_text.setFont(QFont("monospace"))
        self.tabs.addTab(self.events_text, "Kernel Power Events")

        self.raw_json = QPlainTextEdit()
        self.raw_json.setReadOnly(True)
        self.raw_json.setFont(QFont("monospace"))
        self.tabs.addTab(self.raw_json, "Raw Snapshot JSON")

    def setup_i2c_tab(self):
        layout = QVBoxLayout(self.i2c_tab)
        form_box = QGroupBox("Read-only I2C register dump")
        form = QGridLayout(form_box)
        self.i2c_bus_combo = QComboBox()
        self.i2c_addr_edit = QLineEdit("0x2d")
        self.i2c_start_edit = QLineEdit("0x00")
        self.i2c_count_spin = QSpinBox()
        self.i2c_count_spin.setRange(1, 256)
        self.i2c_count_spin.setValue(64)
        self.i2c_dump_button = QPushButton("Dump registers")
        self.i2c_dump_button.clicked.connect(self.dump_i2c)
        self.i2c_rescan_button = QPushButton("Refresh bus/address hints")
        self.i2c_rescan_button.clicked.connect(self.refresh)
        self.i2c_candidate_label = QLabel("Candidate addresses are hints, not identification.")
        self.i2c_candidate_label.setWordWrap(True)
        form.addWidget(QLabel("bus"), 0, 0)
        form.addWidget(self.i2c_bus_combo, 0, 1)
        form.addWidget(QLabel("address"), 0, 2)
        form.addWidget(self.i2c_addr_edit, 0, 3)
        form.addWidget(QLabel("start register"), 1, 0)
        form.addWidget(self.i2c_start_edit, 1, 1)
        form.addWidget(QLabel("count"), 1, 2)
        form.addWidget(self.i2c_count_spin, 1, 3)
        form.addWidget(self.i2c_dump_button, 2, 0, 1, 2)
        form.addWidget(self.i2c_rescan_button, 2, 2, 1, 2)
        form.addWidget(self.i2c_candidate_label, 3, 0, 1, 4)
        layout.addWidget(form_box)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.i2c_scan_table = KeyValueTable(["bus", "address", "candidate note", "scan note"])
        self.i2c_dump_table = KeyValueTable(["register", "hex", "binary", "decimal", "bitfields"])
        splitter.addWidget(self.i2c_scan_table)
        splitter.addWidget(self.i2c_dump_table)
        splitter.setSizes([300, 500])
        layout.addWidget(splitter, 1)

    def toggle_auto(self):
        if self.auto_check.isChecked():
            self.auto_timer.start(int(self.interval_spin.value() * 1000))
        else:
            self.auto_timer.stop()

    def refresh(self):
        self.status_label.setText("probing...")
        self.busy.setRange(0, 0)
        probe = PowerProbe(
            include_kernel_log=self.kernel_log_check.isChecked(),
            kernel_log_lines=self.kernel_lines.value(),
        )
        worker = ProbeWorker(probe)
        worker.signals.done.connect(self.on_probe_done)
        worker.signals.error.connect(self.on_worker_error)
        self.thread_pool.start(worker)

    @Slot(object)
    def on_probe_done(self, result: ProbeResult):
        self.latest = result
        self.history.append(result)
        self.history = self.history[-self.max_history :]
        self.render(result)
        self.status_label.setText(f"sampled {result.timestamp}; errors={len(result.errors)}")
        self.busy.setRange(0, 1)
        self.busy.setValue(1)

    @Slot(str)
    def on_worker_error(self, text: str):
        self.status_label.setText(text)
        self.busy.setRange(0, 1)
        self.busy.setValue(1)
        QMessageBox.warning(self, "Probe error", text)

    def render(self, r: ProbeResult):
        self.render_overview(r)
        self.render_vcgencmd(r)
        self.render_regulators(r)
        self.render_hwmon(r)
        self.render_power_supply(r)
        self.render_usb(r)
        self.render_i2c_scan(r)
        self.debugfs_text.setPlainText(r.debugfs_regulator_summary or "debugfs regulator summary unavailable. Try: sudo mount -t debugfs none /sys/kernel/debug")
        self.events_text.setPlainText("\n".join(r.kernel_power_events) or "No matching power events in selected kernel-log window.")
        self.raw_json.setPlainText(json.dumps(dataclasses.asdict(r), indent=2, sort_keys=True))

    def render_overview(self, r: ProbeResult):
        lines: List[str] = []
        lines.append(f"{APP_NAME} {APP_VERSION}")
        lines.append(f"timestamp: {r.timestamp}")
        lines.append("")
        lines.append("Scope: power-only. Thermal and general system inventory are intentionally excluded.")
        lines.append("")
        t = r.throttled
        lines.append("FIRMWARE POWER FLAGS")
        lines.append(f"  get_throttled raw: {t.get('raw', '')}")
        lines.append(f"  decoded value:      {t.get('hex', t.get('value'))}")
        lines.append(f"  active flags:       {', '.join(t.get('active') or ['none'])}")
        lines.append(f"  historical flags:   {', '.join(t.get('occurred') or ['none'])}")
        if t.get("unknown_bits"):
            lines.append(f"  unknown bits:       {t['unknown_bits']}")
        lines.append("")
        volt_lines = []
        for cmd, data in r.vcgencmd.items():
            if cmd.startswith("measure_volts"):
                volt_lines.append(f"  {cmd:24s} -> {data.get('stdout', '') or data.get('stderr', '')}")
        lines.append("FIRMWARE VOLTAGE COMMANDS")
        lines.extend(volt_lines or ["  none"])
        lines.append("")
        lines.append("DISCOVERY COUNTS")
        lines.append(f"  regulators:          {len(r.regulators)}")
        lines.append(f"  hwmon electrical:    {len(r.hwmon_power)}")
        lines.append(f"  power_supply nodes:  {len(r.power_supplies)}")
        lines.append(f"  USB/Type-C nodes:    {len(r.usb_power)}")
        lines.append(f"  I2C buses:           {len(r.i2c_buses)}")
        lines.append(f"  kernel events:       {len(r.kernel_power_events)}")
        lines.append("")
        lines.append("I2C POWER-RELEVANT CANDIDATES")
        any_candidate = False
        for bus in r.i2c_buses:
            for addr in bus.get("detected", []):
                if addr in COMMON_POWER_I2C_ADDRESSES:
                    any_candidate = True
                    lines.append(f"  /dev/i2c-{bus.get('bus')} addr 0x{addr:02x}: {COMMON_POWER_I2C_ADDRESSES[addr]}")
        if not any_candidate:
            lines.append("  none from built-in heuristic list")
        lines.append("")
        if r.errors:
            lines.append("PROBE ERRORS / PERMISSION LIMITATIONS")
            for e in r.errors:
                lines.append(f"  - {e}")
        else:
            lines.append("PROBE ERRORS / PERMISSION LIMITATIONS")
            lines.append("  none reported")
        self.overview_text.setPlainText("\n".join(lines))

    def render_vcgencmd(self, r: ProbeResult):
        rows = []
        for cmd, data in r.vcgencmd.items():
            note = ""
            if cmd == "get_throttled":
                note = f"active={r.throttled.get('active')} occurred={r.throttled.get('occurred')}"
            rows.append([cmd, data.get("rc"), data.get("stdout"), data.get("stderr"), note])
        self.vc_table.set_rows(rows, mono_columns={0, 2, 3, 4})

    def render_regulators(self, r: ProbeResult):
        rows = []
        for x in r.regulators:
            rows.append([
                x.get("id"),
                x.get("name"),
                x.get("state"),
                x.get("status"),
                x.get("type"),
                x.get("microvolts"),
                x.get("microamps") or x.get("requested_microamps"),
                x.get("num_users"),
                x.get("opmode"),
                x.get("path"),
            ])
        self.reg_table.set_rows(rows, mono_columns={0, 5, 6, 9})

    def render_hwmon(self, r: ProbeResult):
        rows = []
        for x in r.hwmon_power:
            scaled = x.get("scaled")
            if isinstance(scaled, float):
                scaled_s = f"{scaled:.9g}"
            else:
                scaled_s = ""
            rows.append([
                x.get("chip"),
                x.get("attribute"),
                x.get("label"),
                x.get("raw"),
                scaled_s,
                x.get("unit"),
                x.get("quality"),
                x.get("path"),
            ])
        self.hwmon_table.set_rows(rows, mono_columns={1, 3, 4, 7})

    def render_power_supply(self, r: ProbeResult):
        rows = []
        for ps in r.power_supplies:
            name = ps.get("name")
            path = ps.get("path")
            for k, v in sorted(ps.items()):
                if k in {"name", "path"}:
                    continue
                rows.append([name, k, v, path])
        self.ps_table.set_rows(rows, mono_columns={1, 2, 3})

    def render_usb(self, r: ProbeResult):
        rows = []
        for dev in r.usb_power:
            node = dev.get("node")
            path = dev.get("path")
            for k, v in sorted(dev.items()):
                if k in {"node", "path"}:
                    continue
                rows.append([node, k, v, path])
        self.usb_table.set_rows(rows, mono_columns={1, 2, 3})

    def render_i2c_scan(self, r: ProbeResult):
        rows = []
        current = self.i2c_bus_combo.currentText()
        self.i2c_bus_combo.blockSignals(True)
        self.i2c_bus_combo.clear()
        for bus in r.i2c_buses:
            self.i2c_bus_combo.addItem(str(bus.get("bus")))
            scan_note = bus.get("note") or bus.get("scan_stderr") or ""
            detected = bus.get("detected", [])
            if not detected:
                rows.append([bus.get("bus"), "", "", scan_note])
            for addr in detected:
                note = COMMON_POWER_I2C_ADDRESSES.get(addr, "detected device; not in built-in power candidate list")
                rows.append([bus.get("bus"), f"0x{addr:02x}", note, scan_note])
        if current:
            idx = self.i2c_bus_combo.findText(current)
            if idx >= 0:
                self.i2c_bus_combo.setCurrentIndex(idx)
        self.i2c_bus_combo.blockSignals(False)
        self.i2c_scan_table.set_rows(rows, mono_columns={0, 1, 3})

    def dump_i2c(self):
        try:
            bus = int(self.i2c_bus_combo.currentText(), 0)
            addr = int(self.i2c_addr_edit.text().strip(), 0)
            start = int(self.i2c_start_edit.text().strip(), 0)
            count = int(self.i2c_count_spin.value())
        except ValueError as e:
            QMessageBox.warning(self, "Invalid I2C parameters", str(e))
            return
        if not (0x03 <= addr <= 0x77):
            QMessageBox.warning(self, "Invalid I2C address", "Use a 7-bit I2C address in range 0x03..0x77.")
            return
        self.status_label.setText("dumping i2c registers...")
        self.busy.setRange(0, 0)
        worker = I2CWorker(bus, addr, start, count)
        worker.signals.done.connect(self.on_i2c_dump_done)
        worker.signals.error.connect(self.on_worker_error)
        self.thread_pool.start(worker)

    @Slot(object)
    def on_i2c_dump_done(self, result: Dict[str, Any]):
        rows = []
        for x in result.get("values", []):
            reg = x.get("reg")
            val = x.get("value")
            if val is None:
                rows.append([f"0x{reg:02x}", "ERR", "", "", "read failed"])
            else:
                bits = []
                for bit in range(8):
                    if val & (1 << bit):
                        bits.append(f"b{bit}")
                rows.append([f"0x{reg:02x}", f"0x{val:02x}", f"0b{val:08b}", str(val), " ".join(bits)])
        self.i2c_dump_table.set_rows(rows, mono_columns={0, 1, 2, 3, 4})
        errors = result.get("errors", [])
        self.status_label.setText(f"i2c dump complete; errors={len(errors)}")
        self.busy.setRange(0, 1)
        self.busy.setValue(1)
        if errors:
            QMessageBox.information(self, "I2C dump notes", "\n".join(errors[:12]) + ("\n..." if len(errors) > 12 else ""))

    def snapshot_json(self) -> str:
        if self.latest is None:
            return "{}"
        return json.dumps(dataclasses.asdict(self.latest), indent=2, sort_keys=True)

    def copy_json(self):
        QApplication.clipboard().setText(self.snapshot_json())
        self.status_label.setText("snapshot JSON copied")

    def export_json(self):
        if self.latest is None:
            QMessageBox.information(self, "No data", "No snapshot has been collected yet.")
            return
        default = f"pi-power-pmic-{int(time.time())}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Export power snapshot", default, "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.snapshot_json(), encoding="utf-8")
            self.status_label.setText(f"exported {path}")
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Power Engineering")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
