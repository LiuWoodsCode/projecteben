#!/usr/bin/env python3
"""
Raspberry Pi Thermal Lab
========================

A PySide6 expert/engineering-focused thermal instrumentation app for Raspberry Pi.

Scope:
    - Thermal zones from /sys/class/thermal/thermal_zone*
    - Cooling devices from /sys/class/thermal/cooling_device*
    - Pi 5 active cooler / fan data when exposed as a cooling device or hwmon device
    - Firmware thermal/throttling state via vcgencmd
    - Thermal-adjacent firmware measurements: temperature, thermal clocks, throttled flags
    - Raw internal sysfs views for thermal/cooling engineering work

Explicitly not included:
    - General system inventory
    - CPU/RAM/disk/network dashboards
    - Non-thermal system information

Install:
    sudo apt update
    sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtcharts python3-pyside6.qtcore python3-pyside6.qtgui

Or in a venv:
    python3 -m pip install PySide6

Run:
    python3 raspberry_pi_thermal_lab.py

Notes:
    - Some readings require firmware support, kernel driver exposure, or permissions.
    - The Pi 5 fan is normally represented through Linux thermal cooling infrastructure.
    - This app is intentionally verbose and internal-facing.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
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
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableView,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
    HAS_QCHARTS = True
except Exception:
    HAS_QCHARTS = False

SYS_THERMAL = Path("/sys/class/thermal")
SYS_HWMON = Path("/sys/class/hwmon")
VCGENCMD = shutil.which("vcgencmd")

THERMAL_CLOCKS = [
    "arm",
    "core",
    "h264",
    "isp",
    "v3d",
    "uart",
    "pwm",
    "emmc",
    "pixel",
    "vec",
    "hdmi",
    "dpi",
]

THERMAL_VOLTAGES = [
    "core",
    "sdram_c",
    "sdram_i",
    "sdram_p",
]

THROTTLE_BITS = [
    (0, "under_voltage_now", "Under-voltage is currently detected"),
    (1, "freq_capped_now", "ARM frequency is currently capped"),
    (2, "throttled_now", "System is currently throttled"),
    (3, "soft_temp_limit_now", "Soft temperature limit is currently active"),
    (16, "under_voltage_latched", "Under-voltage has occurred since boot"),
    (17, "freq_capped_latched", "ARM frequency capping has occurred since boot"),
    (18, "throttled_latched", "Throttling has occurred since boot"),
    (19, "soft_temp_limit_latched", "Soft temperature limit has occurred since boot"),
]


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(errors="replace").strip()
    except Exception:
        return default


def read_int(path: Path) -> Optional[int]:
    text = read_text(path)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def list_paths(glob: str) -> List[Path]:
    return sorted(Path("/").glob(glob.lstrip("/")), key=lambda p: natural_key(str(p)))


def natural_key(value: str) -> List[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def celsius_from_millideg(value: Optional[int]) -> Optional[float]:
    if value is None:
        return None
    return value / 1000.0


def format_temp(value_c: Optional[float]) -> str:
    if value_c is None:
        return "n/a"
    return f"{value_c:.3f} °C"


def format_hz(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.3f} GHz"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} MHz"
    if value >= 1_000:
        return f"{value / 1_000:.3f} kHz"
    return f"{value} Hz"


def run_command(args: List[str], timeout: float = 1.5) -> Tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 127, "", str(exc)


def vcgencmd(*args: str) -> Optional[str]:
    if not VCGENCMD:
        return None
    code, out, err = run_command([VCGENCMD, *args])
    if code != 0:
        return f"ERROR: {err or out or code}"
    return out


@dataclasses.dataclass
class ThermalTrip:
    index: int
    temp_c: Optional[float]
    trip_type: str
    hysteresis_c: Optional[float]


@dataclasses.dataclass
class ThermalZone:
    name: str
    path: Path
    zone_type: str
    temp_c: Optional[float]
    policy: str
    mode: str
    passive_c: Optional[float]
    trips: List[ThermalTrip]
    raw: Dict[str, str]


@dataclasses.dataclass
class CoolingDevice:
    name: str
    path: Path
    device_type: str
    cur_state: Optional[int]
    max_state: Optional[int]
    stats: Dict[str, str]
    raw: Dict[str, str]

    @property
    def percent(self) -> Optional[float]:
        if self.cur_state is None or self.max_state in (None, 0):
            return None
        return 100.0 * self.cur_state / self.max_state


@dataclasses.dataclass
class HwmonReading:
    chip: str
    path: Path
    key: str
    label: str
    value: str
    raw_path: Path


@dataclasses.dataclass
class FirmwareSnapshot:
    measure_temp_c: Optional[float]
    throttled_raw: Optional[str]
    throttle_flags: Dict[str, bool]
    clocks: Dict[str, Optional[int]]
    voltages: Dict[str, Optional[float]]
    errors: List[str]


@dataclasses.dataclass
class Snapshot:
    timestamp: float
    zones: List[ThermalZone]
    cooling: List[CoolingDevice]
    hwmon: List[HwmonReading]
    firmware: FirmwareSnapshot


class ThermalReader:
    def read_snapshot(self) -> Snapshot:
        return Snapshot(
            timestamp=time.time(),
            zones=self.read_thermal_zones(),
            cooling=self.read_cooling_devices(),
            hwmon=self.read_hwmon(),
            firmware=self.read_firmware(),
        )

    def read_thermal_zones(self) -> List[ThermalZone]:
        zones: List[ThermalZone] = []
        for path in sorted(SYS_THERMAL.glob("thermal_zone*"), key=lambda p: natural_key(p.name)):
            if not path.is_dir():
                continue
            name = path.name
            zone_type = read_text(path / "type", "unknown")
            temp_c = celsius_from_millideg(read_int(path / "temp"))
            policy = read_text(path / "policy", "")
            mode = read_text(path / "mode", "")
            passive_c = celsius_from_millideg(read_int(path / "passive"))
            trips: List[ThermalTrip] = []
            raw: Dict[str, str] = {}

            for child in sorted(path.iterdir(), key=lambda p: natural_key(p.name)):
                if child.is_file():
                    raw[child.name] = read_text(child)

            trip_indices = set()
            for child in path.glob("trip_point_*_temp"):
                match = re.match(r"trip_point_(\d+)_temp", child.name)
                if match:
                    trip_indices.add(int(match.group(1)))

            for idx in sorted(trip_indices):
                trips.append(
                    ThermalTrip(
                        index=idx,
                        temp_c=celsius_from_millideg(read_int(path / f"trip_point_{idx}_temp")),
                        trip_type=read_text(path / f"trip_point_{idx}_type", ""),
                        hysteresis_c=celsius_from_millideg(read_int(path / f"trip_point_{idx}_hyst")),
                    )
                )

            zones.append(
                ThermalZone(
                    name=name,
                    path=path,
                    zone_type=zone_type,
                    temp_c=temp_c,
                    policy=policy,
                    mode=mode,
                    passive_c=passive_c,
                    trips=trips,
                    raw=raw,
                )
            )
        return zones

    def read_cooling_devices(self) -> List[CoolingDevice]:
        devices: List[CoolingDevice] = []
        for path in sorted(SYS_THERMAL.glob("cooling_device*"), key=lambda p: natural_key(p.name)):
            if not path.is_dir():
                continue
            raw: Dict[str, str] = {}
            stats: Dict[str, str] = {}
            for child in sorted(path.iterdir(), key=lambda p: natural_key(p.name)):
                if child.is_file():
                    raw[child.name] = read_text(child)
            stats_path = path / "stats"
            if stats_path.is_dir():
                for child in sorted(stats_path.iterdir(), key=lambda p: natural_key(p.name)):
                    if child.is_file():
                        stats[f"stats/{child.name}"] = read_text(child)
            device_type = read_text(path / "type", "unknown")
            devices.append(
                CoolingDevice(
                    name=path.name,
                    path=path,
                    device_type=device_type,
                    cur_state=read_int(path / "cur_state"),
                    max_state=read_int(path / "max_state"),
                    stats=stats,
                    raw=raw,
                )
            )
        return devices

    def read_hwmon(self) -> List[HwmonReading]:
        readings: List[HwmonReading] = []
        if not SYS_HWMON.exists():
            return readings
        for chip_path in sorted(SYS_HWMON.glob("hwmon*"), key=lambda p: natural_key(p.name)):
            chip = read_text(chip_path / "name", chip_path.name)
            for value_path in sorted(chip_path.glob("*_*input"), key=lambda p: natural_key(p.name)):
                base = value_path.name[:-len("_input")]
                label = read_text(chip_path / f"{base}_label", base)
                raw_value = read_text(value_path)
                value = self.format_hwmon_value(value_path.name, raw_value)
                if self.is_thermal_hwmon(chip, value_path.name, label):
                    readings.append(HwmonReading(chip, chip_path, base, label, value, value_path))
        return readings

    def is_thermal_hwmon(self, chip: str, name: str, label: str) -> bool:
        text = f"{chip} {name} {label}".lower()
        tokens = ["temp", "fan", "pwm", "thermal", "cool", "rpm"]
        return any(token in text for token in tokens)

    def format_hwmon_value(self, name: str, raw: str) -> str:
        try:
            val = int(raw)
        except ValueError:
            return raw
        if name.startswith("temp"):
            return f"{val / 1000.0:.3f} °C ({val} m°C)"
        if name.startswith("fan"):
            return f"{val} RPM"
        if name.startswith("pwm"):
            return f"{val} / 255"
        return str(val)

    def read_firmware(self) -> FirmwareSnapshot:
        errors: List[str] = []
        temp_c: Optional[float] = None
        throttled_raw: Optional[str] = None
        throttle_flags: Dict[str, bool] = {}
        clocks: Dict[str, Optional[int]] = {}
        voltages: Dict[str, Optional[float]] = {}

        if not VCGENCMD:
            errors.append("vcgencmd not found; firmware telemetry unavailable")
            return FirmwareSnapshot(temp_c, throttled_raw, throttle_flags, clocks, voltages, errors)

        temp_out = vcgencmd("measure_temp")
        if temp_out and not temp_out.startswith("ERROR"):
            match = re.search(r"temp=([-+]?\d+(?:\.\d+)?)'C", temp_out)
            if match:
                temp_c = float(match.group(1))
            else:
                errors.append(f"Could not parse measure_temp: {temp_out}")
        elif temp_out:
            errors.append(temp_out)

        throttled_out = vcgencmd("get_throttled")
        if throttled_out and not throttled_out.startswith("ERROR"):
            throttled_raw = throttled_out
            match = re.search(r"0x([0-9a-fA-F]+)", throttled_out)
            if match:
                value = int(match.group(1), 16)
                for bit, name, _desc in THROTTLE_BITS:
                    throttle_flags[name] = bool(value & (1 << bit))
            else:
                errors.append(f"Could not parse get_throttled: {throttled_out}")
        elif throttled_out:
            errors.append(throttled_out)

        for clock in THERMAL_CLOCKS:
            out = vcgencmd("measure_clock", clock)
            parsed: Optional[int] = None
            if out and not out.startswith("ERROR"):
                match = re.search(r"frequency\(\d+\)=(\d+)", out)
                if match:
                    parsed = int(match.group(1))
            clocks[clock] = parsed

        for rail in THERMAL_VOLTAGES:
            out = vcgencmd("measure_volts", rail)
            parsed_v: Optional[float] = None
            if out and not out.startswith("ERROR"):
                match = re.search(r"volt=([-+]?\d+(?:\.\d+)?)V", out)
                if match:
                    parsed_v = float(match.group(1))
            voltages[rail] = parsed_v

        return FirmwareSnapshot(temp_c, throttled_raw, throttle_flags, clocks, voltages, errors)


class DictTableModel(QAbstractTableModel):
    def __init__(self, headers: List[str], rows: Optional[List[List[object]]] = None):
        super().__init__()
        self.headers = headers
        self.rows = rows or []

    def set_rows(self, rows: List[List[object]]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        value = self.rows[index.row()][index.column()]
        if role == Qt.DisplayRole:
            return str(value)
        if role == Qt.FontRole and index.column() == 0:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ForegroundRole:
            row = self.rows[index.row()]
            text = " ".join(str(x).lower() for x in row)
            if "now" in text and "true" in text:
                return QColor("#d7412a")
            if "critical" in text or "hot" in text:
                return QColor("#d7412a")
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]
        return None


class ThermalChart(QWidget):
    def __init__(self):
        super().__init__()
        self.series: Dict[str, QLineSeries] = {}
        self.max_points = 600
        self.axis_x = None
        self.axis_y = None
        layout = QVBoxLayout(self)
        if not HAS_QCHARTS:
            label = QLabel("QtCharts is not installed. Tables and raw telemetry remain available.")
            label.setAlignment(Qt.AlignCenter)
            layout.addWidget(label)
            return
        self.chart = QChart()
        self.chart.setTitle("Thermal zone history")
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignBottom)
        self.axis_x = QValueAxis()
        self.axis_y = QValueAxis()
        self.axis_x.setTitleText("seconds since app start")
        self.axis_y.setTitleText("temperature, °C")
        self.axis_x.setRange(0, 60)
        self.axis_y.setRange(0, 100)
        self.chart.addAxis(self.axis_x, Qt.AlignBottom)
        self.chart.addAxis(self.axis_y, Qt.AlignLeft)
        view = QChartView(self.chart)
        layout.addWidget(view)

    def update_snapshot(self, elapsed: float, snapshot: Snapshot) -> None:
        if not HAS_QCHARTS:
            return
        temps = []
        for zone in snapshot.zones:
            if zone.temp_c is None:
                continue
            temps.append(zone.temp_c)
            if zone.name not in self.series:
                s = QLineSeries()
                s.setName(f"{zone.name}:{zone.zone_type}")
                self.series[zone.name] = s
                self.chart.addSeries(s)
                s.attachAxis(self.axis_x)
                s.attachAxis(self.axis_y)
            s = self.series[zone.name]
            s.append(elapsed, zone.temp_c)
            while s.count() > self.max_points:
                s.remove(0)
        if self.axis_x:
            self.axis_x.setRange(max(0, elapsed - 300), max(60, elapsed))
        if self.axis_y and temps:
            high = max(temps + [80])
            low = min(temps + [20])
            self.axis_y.setRange(max(0, low - 5), high + 10)


class ControlDialog(QDialog):
    def __init__(self, parent: QWidget, device: CoolingDevice):
        super().__init__(parent)
        self.device = device
        self.setWindowTitle(f"Set cooling state: {device.name}")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.info = QLabel(f"{device.path}\nType: {device.device_type}\nCurrent: {device.cur_state}\nMaximum: {device.max_state}")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.info)
        self.state = QSpinBox()
        self.state.setMinimum(0)
        self.state.setMaximum(device.max_state or 0)
        self.state.setValue(device.cur_state or 0)
        form.addRow("Requested cur_state", self.state)
        layout.addLayout(form)
        warning = QLabel(
            "Engineering warning: manual cooling-state writes can fight the kernel thermal governor. "
            "Use only for controlled testing. The app writes only to cur_state; it does not disable policies."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def requested_state(self) -> int:
        return self.state.value()


class RawInspector(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.selector = QComboBox()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.selector)
        layout.addWidget(self.text)
        self.selector.currentIndexChanged.connect(self._changed)
        self.raw_blocks: Dict[str, str] = {}

    def update_snapshot(self, snapshot: Snapshot) -> None:
        current = self.selector.currentText()
        blocks: Dict[str, str] = {}
        for zone in snapshot.zones:
            blocks[f"{zone.name} :: {zone.zone_type}"] = self.format_raw(zone.path, zone.raw)
        for dev in snapshot.cooling:
            raw = dict(dev.raw)
            raw.update(dev.stats)
            blocks[f"{dev.name} :: {dev.device_type}"] = self.format_raw(dev.path, raw)
        for reading in snapshot.hwmon:
            blocks[f"hwmon :: {reading.chip} :: {reading.key}"] = f"path={reading.raw_path}\nlabel={reading.label}\nvalue={reading.value}"
        self.raw_blocks = blocks
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(blocks.keys())
        idx = self.selector.findText(current)
        self.selector.setCurrentIndex(idx if idx >= 0 else 0)
        self.selector.blockSignals(False)
        self._changed()

    def format_raw(self, path: Path, raw: Dict[str, str]) -> str:
        lines = [f"path={path}", ""]
        for key in sorted(raw, key=natural_key):
            lines.append(f"{key}={raw[key]}")
        return "\n".join(lines)

    def _changed(self) -> None:
        self.text.setPlainText(self.raw_blocks.get(self.selector.currentText(), ""))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.reader = ThermalReader()
        self.start_time = time.time()
        self.last_snapshot: Optional[Snapshot] = None
        self.setWindowTitle("Raspberry Pi Thermal Lab")
        self.resize(1380, 880)

        self.zone_model = DictTableModel([
            "Zone", "Type", "Temp", "Policy", "Mode", "Passive", "Trips"
        ])
        self.cooling_model = DictTableModel([
            "Device", "Type", "Current", "Maximum", "Demand", "Stats"
        ])
        self.trip_model = DictTableModel([
            "Zone", "Trip", "Type", "Temp", "Hysteresis"
        ])
        self.hwmon_model = DictTableModel([
            "Chip", "Key", "Label", "Value", "Path"
        ])
        self.fw_model = DictTableModel([
            "Firmware signal", "Value", "Engineering interpretation"
        ])

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self.make_overview_tab(), "Live thermal state")
        self.tabs.addTab(self.make_tables_tab(), "Kernel tables")
        self.tabs.addTab(self.make_firmware_tab(), "Firmware throttling")
        self.raw_inspector = RawInspector()
        self.tabs.addTab(self.raw_inspector, "Raw sysfs inspector")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.interval_ms = 1000
        self.timer.start(self.interval_ms)

        self.make_toolbar()
        self.apply_dark_palette()
        self.refresh()

    def make_toolbar(self) -> None:
        toolbar = QToolBar("Acquisition")
        self.addToolBar(toolbar)
        toolbar.addWidget(QLabel("Refresh interval ms: "))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(100, 60000)
        self.interval_spin.setValue(self.interval_ms)
        self.interval_spin.setSingleStep(100)
        self.interval_spin.valueChanged.connect(self.set_interval)
        toolbar.addWidget(self.interval_spin)
        refresh_action = QAction("Refresh now", self)
        refresh_action.triggered.connect(self.refresh)
        toolbar.addAction(refresh_action)
        self.pause_action = QAction("Pause", self)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.set_paused)
        toolbar.addAction(self.pause_action)
        export_action = QAction("Copy raw report", self)
        export_action.triggered.connect(self.copy_report)
        toolbar.addAction(export_action)

    def make_overview_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        top = QHBoxLayout()
        self.summary_label = QLabel("No data yet")
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.summary_label.setFont(QFont("monospace"))
        top.addWidget(self.summary_label, 1)
        layout.addLayout(top)

        grid = QGridLayout()
        self.temp_boxes: Dict[str, QLabel] = {}
        for i, key in enumerate(["firmware", "hottest_zone", "cooling", "throttle"]):
            box = QGroupBox(key.replace("_", " ").title())
            v = QVBoxLayout(box)
            label = QLabel("n/a")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setFont(QFont("monospace"))
            v.addWidget(label)
            self.temp_boxes[key] = label
            grid.addWidget(box, i // 2, i % 2)
        layout.addLayout(grid)

        self.chart = ThermalChart()
        layout.addWidget(self.chart, 1)
        return widget

    def make_tables_tab(self) -> QWidget:
        widget = QWidget()
        splitter = QSplitter(Qt.Vertical)
        layout = QVBoxLayout(widget)
        layout.addWidget(splitter)
        splitter.addWidget(self.table_group("Thermal zones", self.zone_model))
        splitter.addWidget(self.table_group("Trip points", self.trip_model))
        cooling_group = self.table_group("Cooling devices", self.cooling_model)
        button = QPushButton("Set selected cooling cur_state")
        button.clicked.connect(self.set_selected_cooling_state)
        cooling_group.layout().addWidget(button)
        splitter.addWidget(cooling_group)
        splitter.addWidget(self.table_group("Thermal hwmon readings", self.hwmon_model))
        return widget

    def make_firmware_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.table_group("vcgencmd thermal/throttle telemetry", self.fw_model))
        explainer = QTextEdit()
        explainer.setReadOnly(True)
        explainer.setHtml(
            "<h3>Throttling bit map</h3>"
            "<p><b>Now</b> bits describe the current state. <b>Latched</b> bits describe events since boot.</p>"
            "<ul>"
            + "".join(f"<li><b>bit {bit}</b> {name}: {desc}</li>" for bit, name, desc in THROTTLE_BITS)
            + "</ul>"
            "<p>This panel intentionally treats throttling as thermal-adjacent power/temperature behaviour, not as general system information.</p>"
        )
        layout.addWidget(explainer)
        return widget

    def table_group(self, title: str, model: DictTableModel) -> QGroupBox:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        view = QTableView()
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.setSortingEnabled(True)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        view.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(view)
        if title == "Cooling devices":
            self.cooling_view = view
        return group

    def set_interval(self, value: int) -> None:
        self.interval_ms = value
        if not self.pause_action.isChecked():
            self.timer.start(value)

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.timer.stop()
            self.pause_action.setText("Resume")
        else:
            self.timer.start(self.interval_ms)
            self.pause_action.setText("Pause")

    def refresh(self) -> None:
        snapshot = self.reader.read_snapshot()
        self.last_snapshot = snapshot
        self.update_models(snapshot)
        self.raw_inspector.update_snapshot(snapshot)
        self.chart.update_snapshot(snapshot.timestamp - self.start_time, snapshot)
        self.update_summary(snapshot)

    def update_summary(self, snapshot: Snapshot) -> None:
        hottest = max((z for z in snapshot.zones if z.temp_c is not None), key=lambda z: z.temp_c, default=None)
        active_cooling = [d for d in snapshot.cooling if d.cur_state not in (None, 0)]
        throttle_now = [name for name, active in snapshot.firmware.throttle_flags.items() if active and name.endswith("_now")]
        throttle_latched = [name for name, active in snapshot.firmware.throttle_flags.items() if active and name.endswith("_latched")]

        self.summary_label.setText(
            f"sample_time={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snapshot.timestamp))}\n"
            f"thermal_zones={len(snapshot.zones)} cooling_devices={len(snapshot.cooling)} hwmon_thermal_signals={len(snapshot.hwmon)}\n"
            f"vcgencmd={'available' if VCGENCMD else 'missing'}"
        )
        self.temp_boxes["firmware"].setText(format_temp(snapshot.firmware.measure_temp_c))
        self.temp_boxes["hottest_zone"].setText(
            "n/a" if hottest is None else f"{hottest.name} / {hottest.zone_type}\n{format_temp(hottest.temp_c)}"
        )
        self.temp_boxes["cooling"].setText(
            "none active" if not active_cooling else "\n".join(
                f"{d.name} {d.device_type}: {d.cur_state}/{d.max_state} ({d.percent:.1f}%)"
                if d.percent is not None else f"{d.name} {d.device_type}: {d.cur_state}/{d.max_state}"
                for d in active_cooling
            )
        )
        self.temp_boxes["throttle"].setText(
            f"now: {', '.join(throttle_now) if throttle_now else 'clear'}\n"
            f"latched: {', '.join(throttle_latched) if throttle_latched else 'clear'}\n"
            f"raw: {snapshot.firmware.throttled_raw or 'n/a'}"
        )

    def update_models(self, snapshot: Snapshot) -> None:
        self.zone_model.set_rows([
            [
                z.name,
                z.zone_type,
                format_temp(z.temp_c),
                z.policy or "n/a",
                z.mode or "n/a",
                format_temp(z.passive_c),
                "; ".join(f"{t.index}:{t.trip_type}@{format_temp(t.temp_c)}" for t in z.trips) or "n/a",
            ]
            for z in snapshot.zones
        ])
        self.trip_model.set_rows([
            [z.name, t.index, t.trip_type or "n/a", format_temp(t.temp_c), format_temp(t.hysteresis_c)]
            for z in snapshot.zones
            for t in z.trips
        ])
        self.cooling_model.set_rows([
            [
                d.name,
                d.device_type,
                "n/a" if d.cur_state is None else d.cur_state,
                "n/a" if d.max_state is None else d.max_state,
                "n/a" if d.percent is None else f"{d.percent:.1f}%",
                "; ".join(f"{k}={v}" for k, v in d.stats.items()) or "n/a",
            ]
            for d in snapshot.cooling
        ])
        self.hwmon_model.set_rows([
            [h.chip, h.key, h.label, h.value, str(h.raw_path)]
            for h in snapshot.hwmon
        ])
        fw_rows: List[List[object]] = []
        fw = snapshot.firmware
        fw_rows.append(["measure_temp", format_temp(fw.measure_temp_c), "Firmware-reported SoC temperature"])
        fw_rows.append(["get_throttled", fw.throttled_raw or "n/a", "Raw firmware throttle register"])
        for bit, name, desc in THROTTLE_BITS:
            fw_rows.append([f"bit_{bit}_{name}", fw.throttle_flags.get(name, False), desc])
        for clock, hz in fw.clocks.items():
            fw_rows.append([f"clock_{clock}", format_hz(hz), "Thermal/power policy may affect this domain"])
        for rail, volts in fw.voltages.items():
            fw_rows.append([f"voltage_{rail}", "n/a" if volts is None else f"{volts:.4f} V", "Firmware voltage rail measurement"])
        for err in fw.errors:
            fw_rows.append(["error", err, "Telemetry unavailable or parse failure"])
        self.fw_model.set_rows(fw_rows)

    def selected_cooling_device(self) -> Optional[CoolingDevice]:
        if not self.last_snapshot:
            return None
        index = self.cooling_view.currentIndex()
        if not index.isValid():
            return None
        source_row = self.cooling_view.model().index(index.row(), 0).data()
        for dev in self.last_snapshot.cooling:
            if dev.name == source_row:
                return dev
        return None

    def set_selected_cooling_state(self) -> None:
        dev = self.selected_cooling_device()
        if dev is None:
            QMessageBox.information(self, "No cooling device selected", "Select a cooling device row first.")
            return
        if dev.max_state is None:
            QMessageBox.warning(self, "Unsupported", "This cooling device does not expose max_state.")
            return
        dialog = ControlDialog(self, dev)
        if dialog.exec() != QDialog.Accepted:
            return
        path = dev.path / "cur_state"
        try:
            path.write_text(str(dialog.requested_state()))
        except PermissionError:
            QMessageBox.critical(
                self,
                "Permission denied",
                f"Could not write {path}. Try running with sufficient privileges or adjust udev/sysfs permissions for lab use.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Write failed", f"Could not write {path}: {exc}")
        self.refresh()

    def copy_report(self) -> None:
        if not self.last_snapshot:
            return
        QApplication.clipboard().setText(self.build_report(self.last_snapshot))

    def build_report(self, snapshot: Snapshot) -> str:
        lines = []
        lines.append("Raspberry Pi Thermal Lab raw report")
        lines.append(f"timestamp={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snapshot.timestamp))}")
        lines.append("")
        lines.append("[thermal_zones]")
        for z in snapshot.zones:
            lines.append(f"{z.name} type={z.zone_type} temp={format_temp(z.temp_c)} policy={z.policy} mode={z.mode}")
            for t in z.trips:
                lines.append(f"  trip{t.index} type={t.trip_type} temp={format_temp(t.temp_c)} hyst={format_temp(t.hysteresis_c)}")
        lines.append("")
        lines.append("[cooling_devices]")
        for d in snapshot.cooling:
            lines.append(f"{d.name} type={d.device_type} cur={d.cur_state} max={d.max_state} percent={d.percent}")
            for k, v in d.stats.items():
                lines.append(f"  {k}={v}")
        lines.append("")
        lines.append("[hwmon]")
        for h in snapshot.hwmon:
            lines.append(f"{h.chip} {h.key} {h.label} {h.value} path={h.raw_path}")
        lines.append("")
        lines.append("[firmware]")
        fw = snapshot.firmware
        lines.append(f"measure_temp={format_temp(fw.measure_temp_c)}")
        lines.append(f"get_throttled={fw.throttled_raw}")
        for k, v in fw.throttle_flags.items():
            lines.append(f"{k}={v}")
        for k, v in fw.clocks.items():
            lines.append(f"clock_{k}={format_hz(v)}")
        for k, v in fw.voltages.items():
            lines.append(f"voltage_{k}={v}")
        for err in fw.errors:
            lines.append(f"error={err}")
        return "\n".join(lines)

    def apply_dark_palette(self) -> None:
        pass


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Raspberry Pi Thermal Lab")
    app.setOrganizationName("Thermal Engineering")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
