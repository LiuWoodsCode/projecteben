#!/usr/bin/env python3
"""
Raspberry Pi Input Lab
======================

A PySide6 engineering/diagnostic application for testing connected keyboards,
mice, touchpads, barcode scanners, macro pads, and other Linux evdev input
hardware on Raspberry Pi OS and other Linux systems.

Features
--------
- Full support for EV_KEY (including power, sleep, wake, lid, tablet mode, etc.)
- Support for EV_SW (switch devices like lid open/close, tablet mode, dock state)
- Support for EV_PWR and other system-level input classes when exposed by kernel
- Captures EV_KEY, EV_REL, EV_ABS, EV_MSC, EV_LED, EV_SYN, and unknown events.
- Enumerates /dev/input/event* devices through python-evdev.
- Captures EV_KEY, EV_REL, EV_ABS, EV_MSC, EV_LED, EV_SYN, and unknown events.
- Per-device event counters, timing, rate, last event age, and capability view.
- Keyboard matrix/heatmap with press/release/repeat tracking.
- Mouse movement telemetry: dx/dy, wheel, accumulated path, buttons.
- Raw event log with filtering, freeze, clear, and JSONL export.
- Device grab support for exclusive capture during low-level testing.
- Works well over X11/Wayland as long as the user has permission to read evdev.

Install
-------
    sudo apt update
    sudo apt install -y python3-pip python3-pyside6 python3-evdev

Or in a virtual environment:
    python3 -m venv .venv
    . .venv/bin/activate
    pip install PySide6 evdev

Permissions
-----------
Reading /dev/input/event* usually requires root or membership in the input group:
    sudo usermod -aG input "$USER"
Then log out and back in.

Run
---
    python3 raspberry_pi_input_lab_pyside6.py

For exclusive device testing, launch with sufficient privileges or input-group access.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen, QBrush
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
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    import evdev
    from evdev import InputDevice, ecodes, categorize
except Exception as exc:  # pragma: no cover - displayed in GUI/runtime
    evdev = None
    InputDevice = None
    ecodes = None
    categorize = None
    EVDEV_IMPORT_ERROR = exc
else:
    EVDEV_IMPORT_ERROR = None

APP_NAME = "Raspberry Pi Input Lab"
MAX_LOG_ROWS = 5000
MAX_PATH_POINTS = 2500

KEY_LAYOUT_ROWS = [
    ["KEY_ESC", "KEY_F1", "KEY_F2", "KEY_F3", "KEY_F4", "KEY_F5", "KEY_F6", "KEY_F7", "KEY_F8", "KEY_F9", "KEY_F10", "KEY_F11", "KEY_F12"],
    ["KEY_GRAVE", "KEY_1", "KEY_2", "KEY_3", "KEY_4", "KEY_5", "KEY_6", "KEY_7", "KEY_8", "KEY_9", "KEY_0", "KEY_MINUS", "KEY_EQUAL", "KEY_BACKSPACE"],
    ["KEY_TAB", "KEY_Q", "KEY_W", "KEY_E", "KEY_R", "KEY_T", "KEY_Y", "KEY_U", "KEY_I", "KEY_O", "KEY_P", "KEY_LEFTBRACE", "KEY_RIGHTBRACE", "KEY_BACKSLASH"],
    ["KEY_CAPSLOCK", "KEY_A", "KEY_S", "KEY_D", "KEY_F", "KEY_G", "KEY_H", "KEY_J", "KEY_K", "KEY_L", "KEY_SEMICOLON", "KEY_APOSTROPHE", "KEY_ENTER"],
    ["KEY_LEFTSHIFT", "KEY_Z", "KEY_X", "KEY_C", "KEY_V", "KEY_B", "KEY_N", "KEY_M", "KEY_COMMA", "KEY_DOT", "KEY_SLASH", "KEY_RIGHTSHIFT"],
    ["KEY_LEFTCTRL", "KEY_LEFTMETA", "KEY_LEFTALT", "KEY_SPACE", "KEY_RIGHTALT", "KEY_RIGHTMETA", "KEY_COMPOSE", "KEY_RIGHTCTRL"],
]

MOUSE_BUTTONS = [
    "BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE", "BTN_SIDE", "BTN_EXTRA", "BTN_FORWARD", "BTN_BACK", "BTN_TASK",
    "BTN_TOUCH", "BTN_STYLUS", "BTN_STYLUS2",
]


def now_ns() -> int:
    return time.monotonic_ns()


def ns_to_ms(ns: int) -> float:
    return ns / 1_000_000.0


def safe_code_name(code_type: int, code: int) -> str:
    if ecodes is None:
        return str(code)
    try:
        name = ecodes.bytype.get(code_type, {}).get(code, str(code))
        if isinstance(name, list):
            return "/".join(name)
        return str(name)
    except Exception:
        return str(code)


def safe_type_name(code_type: int) -> str:
    if ecodes is None:
        return str(code_type)
    return str(ecodes.EV.get(code_type, code_type))


def code_value(symbol: str) -> Optional[int]:
    if ecodes is None:
        return None
    value = getattr(ecodes, symbol, None)
    return value if isinstance(value, int) else None


@dataclass
class DeviceInfo:
    path: str
    name: str
    phys: str
    uniq: str
    vendor: int
    product: int
    version: int
    bustype: int
    capabilities: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class InputEventDTO:
    device_path: str
    device_name: str
    sec: int
    usec: int
    event_type: int
    event_code: int
    event_value: int
    type_name: str
    code_name: str
    host_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_path": self.device_path,
            "device_name": self.device_name,
            "kernel_time_sec": self.sec,
            "kernel_time_usec": self.usec,
            "event_type": self.event_type,
            "event_code": self.event_code,
            "event_value": self.event_value,
            "type_name": self.type_name,
            "code_name": self.code_name,
            "host_monotonic_ns": self.host_ns,
        }


@dataclass
class DeviceStats:
    total: int = 0
    by_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    first_ns: Optional[int] = None
    last_ns: Optional[int] = None
    last_event_text: str = ""

    def observe(self, event: InputEventDTO) -> None:
        self.total += 1
        self.by_type[event.type_name] += 1
        if self.first_ns is None:
            self.first_ns = event.host_ns
        self.last_ns = event.host_ns
        self.last_event_text = f"{event.type_name} {event.code_name} {event.event_value}"

    def rate_hz(self) -> float:
        if self.first_ns is None or self.last_ns is None or self.last_ns <= self.first_ns:
            return 0.0
        return self.total / ((self.last_ns - self.first_ns) / 1_000_000_000.0)


class BatchAggregator(QObject):
    """Coalesces incoming batches on a worker thread and emits at a fixed cadence."""
    batch_out = Signal(object)  # list[InputEventDTO]

    def __init__(self, interval_ms: int = 50) -> None:
        super().__init__()
        self._buf = []  # type: list[InputEventDTO]
        self._interval = interval_ms
        self._timer: Optional[QTimer] = None

    @Slot()
    def start(self) -> None:
        # Create timer in this thread and parent it correctly
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    @Slot()
    def stop(self) -> None:
        # Must run in the same thread as the timer
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    @Slot(object)
    def enqueue(self, events: object) -> None:
        if isinstance(events, list):
            self._buf.extend(events)
        else:
            self._buf.append(events)

    @Slot()
    def _flush(self) -> None:
        if not self._buf:
            return
        out = self._buf
        self._buf = []
        self.batch_out.emit(out)


class EvdevReader(QObject):
    event_received = Signal(object)  # Emits list[InputEventDTO] batches, not individual events
    device_error = Signal(str, str)
    started_device = Signal(object)
    stopped_device = Signal(str)

    def __init__(self, info: DeviceInfo, grab: bool = False) -> None:
        super().__init__()
        self.info = info
        self.grab = grab
        self._running = True
        self._device: Optional[InputDevice] = None

    @Slot()
    def run(self) -> None:
        try:
            self._device = InputDevice(self.info.path)
            if self.grab:
                try:
                    self._device.grab()
                except Exception as exc:
                    self.device_error.emit(self.info.path, f"Could not grab device: {exc}")
            self.started_device.emit(self.info)

            batch: list[InputEventDTO] = []
            last_emit_ns = now_ns()
            batch_size = 96
            max_batch_age_ns = 12_000_000  # 12 ms; prevents Qt signal storms while keeping latency low.

            while self._running:
                try:
                    event = self._device.read_one()
                    current_ns = now_ns()

                    if event is not None:
                        batch.append(InputEventDTO(
                            device_path=self.info.path,
                            device_name=self.info.name,
                            sec=event.sec,
                            usec=event.usec,
                            event_type=event.type,
                            event_code=event.code,
                            event_value=event.value,
                            type_name=safe_type_name(event.type),
                            code_name=safe_code_name(event.type, event.code),
                            host_ns=current_ns,
                        ))

                    if batch and (len(batch) >= batch_size or current_ns - last_emit_ns >= max_batch_age_ns):
                        self.event_received.emit(batch)
                        batch = []
                        last_emit_ns = current_ns

                    if event is None:
                        time.sleep(0.001)

                except OSError as exc:
                    self.device_error.emit(self.info.path, f"Read failed: {exc}")
                    break
                except Exception as exc:
                    self.device_error.emit(self.info.path, f"Unexpected read error: {exc}")
                    break

            if batch:
                self.event_received.emit(batch)

        except Exception as exc:
            self.device_error.emit(self.info.path, f"Open failed: {exc}")
        finally:
            try:
                if self._device and self.grab:
                    self._device.ungrab()
            except Exception:
                pass
            try:
                if self._device:
                    self._device.close()
            except Exception:
                pass
            self.stopped_device.emit(self.info.path)

    def stop(self) -> None:
        self._running = False


class KeyboardMatrix(QWidget):
    key_clicked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._state: dict[str, int] = defaultdict(int)
        self._counts: dict[str, int] = defaultdict(int)
        self._last_ns: dict[str, int] = {}
        self.setMinimumHeight(290)
        self.setMouseTracking(True)
        self._rects: dict[str, QRectF] = {}

    def set_key_state(self, key_name: str, value: int, timestamp_ns: int) -> None:
        self._state[key_name] = value
        if value == 1:
            self._counts[key_name] += 1
        self._last_ns[key_name] = timestamp_ns
        self.update()

    def reset(self) -> None:
        self._state.clear()
        self._counts.clear()
        self._last_ns.clear()
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        for key, rect in self._rects.items():
            if rect.contains(pos):
                self.key_clicked.emit(key)
                return

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 24))
        self._rects.clear()

        margin = 10
        gap = 5
        row_h = max(30, int((self.height() - margin * 2 - gap * (len(KEY_LAYOUT_ROWS) - 1)) / len(KEY_LAYOUT_ROWS)))
        font = QFont("monospace", 8)
        painter.setFont(font)

        for row_index, row in enumerate(KEY_LAYOUT_ROWS):
            total_weight = sum(3.2 if key == "KEY_SPACE" else 1.7 if key in {"KEY_BACKSPACE", "KEY_ENTER", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_CAPSLOCK"} else 1.0 for key in row)
            usable_w = self.width() - margin * 2 - gap * (len(row) - 1)
            x = margin
            y = margin + row_index * (row_h + gap)
            for key in row:
                weight = 3.2 if key == "KEY_SPACE" else 1.7 if key in {"KEY_BACKSPACE", "KEY_ENTER", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_CAPSLOCK"} else 1.0
                w = usable_w * weight / total_weight
                rect = QRectF(x, y, w, row_h)
                self._rects[key] = rect

                state = self._state.get(key, 0)
                count = self._counts.get(key, 0)
                age_ms = ns_to_ms(now_ns() - self._last_ns.get(key, now_ns())) if key in self._last_ns else None

                if state == 1:
                    fill = QColor(255, 64, 190)
                    text = QColor(10, 10, 12)
                elif state == 2:
                    fill = QColor(255, 190, 80)
                    text = QColor(10, 10, 12)
                elif count:
                    intensity = min(120, 30 + count * 12)
                    fill = QColor(65 + intensity, 45, 90 + intensity // 2)
                    text = QColor(235, 235, 245)
                else:
                    fill = QColor(38, 38, 50)
                    text = QColor(210, 210, 220)

                painter.setPen(QPen(QColor(90, 90, 115), 1))
                painter.setBrush(QBrush(fill))
                painter.drawRoundedRect(rect, 6, 6)
                painter.setPen(text)
                label = key.replace("KEY_", "")
                painter.drawText(rect.adjusted(4, 3, -4, -3), Qt.AlignTop | Qt.AlignHCenter, label)
                sub = f"n={count}"
                if age_ms is not None and age_ms < 2000:
                    sub += f"  {age_ms:.0f}ms"
                painter.drawText(rect.adjusted(4, 3, -4, -3), Qt.AlignBottom | Qt.AlignHCenter, sub)
                x += w + gap


class MouseTelemetry(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.dx_total = 0
        self.dy_total = 0
        self.wheel_total = 0
        self.hwheeel_total = 0
        self.buttons: dict[str, int] = defaultdict(int)
        self.points: deque[QPointF] = deque(maxlen=MAX_PATH_POINTS)
        self.cursor = QPointF(0, 0)
        self.points.append(QPointF(0, 0))
        self.setMinimumHeight(300)

    def reset(self) -> None:
        self.dx_total = 0
        self.dy_total = 0
        self.wheel_total = 0
        self.hwheeel_total = 0
        self.buttons.clear()
        self.points.clear()
        self.cursor = QPointF(0, 0)
        self.points.append(QPointF(0, 0))
        self.update()

    def observe(self, event: InputEventDTO) -> None:
        if event.type_name == "EV_REL":
            if event.code_name == "REL_X":
                self.dx_total += event.event_value
                self.cursor += QPointF(event.event_value, 0)
            elif event.code_name == "REL_Y":
                self.dy_total += event.event_value
                self.cursor += QPointF(0, event.event_value)
            elif event.code_name in {"REL_WHEEL", "REL_WHEEL_HI_RES"}:
                self.wheel_total += event.event_value
            elif event.code_name in {"REL_HWHEEL", "REL_HWHEEL_HI_RES"}:
                self.hwheeel_total += event.event_value
            self.points.append(QPointF(self.cursor))
            self.update()
        elif event.type_name == "EV_KEY" and event.code_name.startswith("BTN_"):
            self.buttons[event.code_name] = event.event_value
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(14, 16, 22))

        bounds = self.rect().adjusted(14, 14, -14, -72)
        painter.setPen(QPen(QColor(75, 80, 100), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(bounds, 8, 8)

        cx = bounds.center().x()
        cy = bounds.center().y()
        painter.setPen(QPen(QColor(60, 65, 80), 1, Qt.DashLine))
        painter.drawLine(bounds.left(), cy, bounds.right(), cy)
        painter.drawLine(cx, bounds.top(), cx, bounds.bottom())

        if len(self.points) > 1:
            xs = [p.x() for p in self.points]
            ys = [p.y() for p in self.points]
            max_abs = max(1.0, max(abs(min(xs)), abs(max(xs)), abs(min(ys)), abs(max(ys))))
            scale = min(bounds.width(), bounds.height()) * 0.45 / max_abs
            painter.setPen(QPen(QColor(163, 239, 255), 2))
            prev = None
            for p in self.points:
                mapped = QPointF(cx + p.x() * scale, cy + p.y() * scale)
                if prev is not None:
                    painter.drawLine(prev, mapped)
                prev = mapped
            painter.setBrush(QBrush(QColor(255, 64, 190)))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(prev, 4, 4)

        painter.setPen(QColor(230, 230, 240))
        y = self.height() - 54
        text = f"dx={self.dx_total}  dy={self.dy_total}  wheel={self.wheel_total}  hwheel={self.hwheeel_total}  samples={len(self.points)}"
        painter.drawText(16, y, text)
        y += 24
        pressed = [b for b, v in sorted(self.buttons.items()) if v]
        painter.drawText(16, y, "buttons_down=" + (", ".join(pressed) if pressed else "none"))


class DeviceTable(QTableWidget):
    def __init__(self) -> None:
        super().__init__(0, 9)
        self.setHorizontalHeaderLabels(["Enabled", "Path", "Name", "Bus", "Vendor", "Product", "Events", "Rate Hz", "Last Event"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self._paths: list[str] = []

    def set_devices(self, devices: list[DeviceInfo]) -> None:
        checked_paths = set()
        for row, path in enumerate(self._paths):
            item = self.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                checked_paths.add(path)

        self._paths = [d.path for d in devices]
        self.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            enabled = QTableWidgetItem("")
            enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            enabled.setCheckState(Qt.Checked if dev.path in checked_paths or not checked_paths else Qt.Unchecked)
            self.setItem(row, 0, enabled)
            for col, value in enumerate([
                dev.path,
                dev.name,
                str(dev.bustype),
                f"0x{dev.vendor:04x}",
                f"0x{dev.product:04x}",
                "0",
                "0.00",
                "",
            ], start=1):
                self.setItem(row, col, QTableWidgetItem(value))

    def enabled_paths(self) -> set[str]:
        result = set()
        for row, path in enumerate(self._paths):
            item = self.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.add(path)
        return result

    def update_stats(self, stats: dict[str, DeviceStats]) -> None:
        for row, path in enumerate(self._paths):
            st = stats.get(path)
            if not st:
                continue
            self.item(row, 6).setText(str(st.total))
            self.item(row, 7).setText(f"{st.rate_hz():.2f}")
            self.item(row, 8).setText(st.last_event_text)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 900)
        self.devices: list[DeviceInfo] = []
        self.stats: dict[str, DeviceStats] = defaultdict(DeviceStats)
        self.raw_events: deque[dict[str, Any]] = deque(maxlen=MAX_LOG_ROWS)
        self.threads: dict[str, QThread] = {}
        self.agg_thread: Optional[QThread] = None
        self.aggregator: Optional[BatchAggregator] = None
        self.readers: dict[str, EvdevReader] = {}
        self.capture_active = False
        self.last_event_ns: Optional[int] = None
        self._pending_visual_update = False
        self._pending_log_events: list[dict[str, Any]] = []
        self._dropped_log_events = 0

        self._build_ui()
        self._wire_actions()

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._refresh_ui)
        self.ui_timer.start(250)

        self.log_flush_timer = QTimer(self)
        self.log_flush_timer.timeout.connect(self.flush_log_batch)
        self.log_flush_timer.start(150)

        # Start aggregator thread to decouple high-rate input from UI
        self.agg_thread = QThread(self)
        self.aggregator = BatchAggregator(interval_ms=50)
        self.aggregator.moveToThread(self.agg_thread)
        self.agg_thread.started.connect(self.aggregator.start)
        self.aggregator.batch_out.connect(self.handle_event)
        self.agg_thread.start()

        self.scan_devices()

    def _build_ui(self) -> None:
        toolbar = QToolBar("Capture")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_scan = QAction("Rescan", self)
        self.action_start = QAction("Start Capture", self)
        self.action_stop = QAction("Stop", self)
        self.action_stop.setEnabled(False)
        self.action_export = QAction("Export JSONL", self)
        self.action_clear = QAction("Clear", self)
        toolbar.addAction(self.action_scan)
        toolbar.addSeparator()
        toolbar.addAction(self.action_start)
        toolbar.addAction(self.action_stop)
        toolbar.addSeparator()
        toolbar.addAction(self.action_export)
        toolbar.addAction(self.action_clear)

        self.grab_check = QCheckBox("Exclusive grab")
        self.freeze_check = QCheckBox("Freeze log")
        self.type_filter = QComboBox()
        self.type_filter.addItems(["All", "EV_KEY", "EV_REL", "EV_ABS", "EV_MSC", "EV_LED", "EV_SYN", "Other"])
        self.text_filter = QLineEdit()
        self.text_filter.setPlaceholderText("Filter code/device text")
        toolbar.addWidget(QLabel("   "))
        toolbar.addWidget(self.grab_check)
        toolbar.addWidget(QLabel("   Type "))
        toolbar.addWidget(self.type_filter)
        toolbar.addWidget(QLabel("   Text "))
        toolbar.addWidget(self.text_filter)
        toolbar.addWidget(QLabel("   "))
        toolbar.addWidget(self.freeze_check)

        root = QSplitter(Qt.Horizontal)
        self.setCentralWidget(root)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.device_table = DeviceTable()
        left_layout.addWidget(QLabel("/dev/input event devices"))
        left_layout.addWidget(self.device_table)

        self.capability_view = QTextEdit()
        self.capability_view.setReadOnly(True)
        self.capability_view.setMinimumHeight(260)
        left_layout.addWidget(QLabel("Selected device capabilities"))
        left_layout.addWidget(self.capability_view)
        root.addWidget(left)

        tabs = QTabWidget()
        root.addWidget(tabs)
        root.setSizes([520, 960])

        dashboard = QWidget()
        dash_layout = QVBoxLayout(dashboard)

        metrics_group = QGroupBox("Global capture metrics")
        metrics_layout = QGridLayout(metrics_group)
        self.metric_total = QLabel("0")
        self.metric_rate = QLabel("0.00 Hz")
        self.metric_age = QLabel("never")
        self.metric_devices = QLabel("0")
        self.metric_keys_down = QLabel("none")
        self.metric_mouse = QLabel("dx=0 dy=0")
        for label in [self.metric_total, self.metric_rate, self.metric_age, self.metric_devices, self.metric_keys_down, self.metric_mouse]:
            label.setFont(QFont("monospace", 11))
        metrics_layout.addWidget(QLabel("Total events"), 0, 0)
        metrics_layout.addWidget(self.metric_total, 0, 1)
        metrics_layout.addWidget(QLabel("Aggregate rate"), 0, 2)
        metrics_layout.addWidget(self.metric_rate, 0, 3)
        metrics_layout.addWidget(QLabel("Last event age"), 1, 0)
        metrics_layout.addWidget(self.metric_age, 1, 1)
        metrics_layout.addWidget(QLabel("Capturing devices"), 1, 2)
        metrics_layout.addWidget(self.metric_devices, 1, 3)
        metrics_layout.addWidget(QLabel("Keys down"), 2, 0)
        metrics_layout.addWidget(self.metric_keys_down, 2, 1, 1, 3)
        metrics_layout.addWidget(QLabel("Mouse totals"), 3, 0)
        metrics_layout.addWidget(self.metric_mouse, 3, 1, 1, 3)
        dash_layout.addWidget(metrics_group)

        self.keyboard_matrix = KeyboardMatrix()
        self.mouse_view = MouseTelemetry()
        dash_layout.addWidget(QLabel("Keyboard matrix / heat map"))
        dash_layout.addWidget(self.keyboard_matrix)
        dash_layout.addWidget(QLabel("Mouse relative motion plot"))
        dash_layout.addWidget(self.mouse_view)
        tabs.addTab(dashboard, "Dashboard")

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.log_table = QTableWidget(0, 9)
        self.log_table.setHorizontalHeaderLabels(["#", "Host ms", "Device", "Path", "Type", "Code", "Value", "Kernel sec.usec", "Raw"])
        self.log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        raw_layout.addWidget(self.log_table)
        tabs.addTab(raw_tab, "Raw Events")

        analysis_tab = QWidget()
        analysis_layout = QVBoxLayout(analysis_tab)
        self.analysis_text = QTextEdit()
        self.analysis_text.setReadOnly(True)
        self.analysis_text.setFont(QFont("monospace", 10))
        analysis_layout.addWidget(self.analysis_text)
        tabs.addTab(analysis_tab, "Counters")

        self.status = QLabel("Ready")
        self.statusBar().addPermanentWidget(self.status, 1)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #15151c; color: #eeeeff; }
            QTableWidget, QTextEdit, QLineEdit, QComboBox { background: #20202a; color: #eeeeff; border: 1px solid #44445c; }
            QHeaderView::section { background: #2d2a4d; color: #ffffff; padding: 4px; border: 1px solid #44445c; }
            QPushButton, QToolButton { background: #2d2a4d; color: #ffffff; border: 1px solid #8e5faf; padding: 5px; border-radius: 4px; }
            QPushButton:hover, QToolButton:hover { background: #3b3568; }
            QGroupBox { border: 1px solid #4c496e; border-radius: 6px; margin-top: 8px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        """)

    def _wire_actions(self) -> None:
        self.action_scan.triggered.connect(self.scan_devices)
        self.action_start.triggered.connect(self.start_capture)
        self.action_stop.triggered.connect(self.stop_capture)
        self.action_export.triggered.connect(self.export_jsonl)
        self.action_clear.triggered.connect(self.clear_state)
        self.device_table.itemSelectionChanged.connect(self.show_selected_capabilities)
        self.text_filter.textChanged.connect(self.rebuild_log_table)
        self.type_filter.currentTextChanged.connect(self.rebuild_log_table)

    @Slot()
    def scan_devices(self) -> None:
        if evdev is None:
            QMessageBox.critical(self, "Missing dependency", f"python-evdev is not available:\n{EVDEV_IMPORT_ERROR}")
            return

        found: list[DeviceInfo] = []
        for path in sorted(evdev.list_devices()):
            try:
                dev = InputDevice(path)
                caps_raw = dev.capabilities(verbose=True)
                caps: dict[str, list[str]] = {}
                for event_type, entries in caps_raw.items():
                    if isinstance(event_type, tuple):
                        type_name = event_type[0]
                    else:
                        type_name = str(event_type)
                    names = []
                    for entry in entries:
                        if isinstance(entry, tuple) and entry:
                            if isinstance(entry[0], tuple):
                                names.append(str(entry[0][0]))
                            else:
                                names.append(str(entry[0]))
                        else:
                            names.append(str(entry))
                    caps[type_name] = names
                info = dev.info
                found.append(DeviceInfo(
                    path=path,
                    name=dev.name or "unknown",
                    phys=dev.phys or "",
                    uniq=dev.uniq or "",
                    vendor=info.vendor,
                    product=info.product,
                    version=info.version,
                    bustype=info.bustype,
                    capabilities=caps,
                ))
                dev.close()
            except Exception as exc:
                found.append(DeviceInfo(path=path, name=f"<unreadable: {exc}>", phys="", uniq="", vendor=0, product=0, version=0, bustype=0))
        self.devices = found
        self.device_table.set_devices(found)
        self.status.setText(f"Found {len(found)} event devices")
        self.show_selected_capabilities()

    @Slot()
    def show_selected_capabilities(self) -> None:
        row = self.device_table.currentRow()
        if row < 0 or row >= len(self.devices):
            self.capability_view.setPlainText("Select a device.")
            return
        dev = self.devices[row]
        payload = {
            "path": dev.path,
            "name": dev.name,
            "phys": dev.phys,
            "uniq": dev.uniq,
            "ids": {
                "bustype": dev.bustype,
                "vendor": f"0x{dev.vendor:04x}",
                "product": f"0x{dev.product:04x}",
                "version": dev.version,
            },
            "capabilities": dev.capabilities,
        }
        self.capability_view.setPlainText(json.dumps(payload, indent=2, sort_keys=True))

    @Slot()
    def start_capture(self) -> None:
        if self.capture_active:
            return
        enabled = self.device_table.enabled_paths()
        if not enabled:
            QMessageBox.warning(self, "No devices", "Enable at least one input device.")
            return
        self.capture_active = True
        self.action_start.setEnabled(False)
        self.action_stop.setEnabled(True)
        self.action_scan.setEnabled(False)
        self.status.setText("Capture running")

        for dev in self.devices:
            if dev.path not in enabled:
                continue
            thread = QThread(self)
            reader = EvdevReader(dev, grab=self.grab_check.isChecked())
            reader.moveToThread(thread)
            thread.started.connect(reader.run)
            reader.event_received.connect(self.aggregator.enqueue)
            reader.device_error.connect(self.handle_device_error)
            reader.stopped_device.connect(self.handle_device_stopped)
            thread.finished.connect(reader.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self.threads[dev.path] = thread
            self.readers[dev.path] = reader
            thread.start()

    @Slot()
    def stop_capture(self) -> None:
        for reader in list(self.readers.values()):
            reader.stop()
        for thread in list(self.threads.values()):
            thread.quit()
            thread.wait(1000)
        self.threads.clear()
        self.readers.clear()
        self.capture_active = False
        self.action_start.setEnabled(True)
        self.action_stop.setEnabled(False)
        self.action_scan.setEnabled(True)
        self.status.setText("Capture stopped")

    @Slot(object)
    def handle_event(self, events: object) -> None:
        # The reader emits batches. Accepting a single DTO keeps this slot tolerant of older callers.
        if isinstance(events, InputEventDTO):
            event_batch = [events]
        else:
            event_batch = list(events)

        for event in event_batch:
            self.stats[event.device_path].observe(event)
            self.last_event_ns = event.host_ns
            as_dict = event.to_dict()
            self.raw_events.append(as_dict)

            if event.type_name == "EV_KEY":
                # Includes ordinary keyboard keys, mouse buttons, KEY_POWER, KEY_SLEEP, KEY_WAKEUP, etc.
                if event.code_name.startswith("KEY_"):
                    self.keyboard_matrix._state[event.code_name] = event.event_value
                    if event.event_value == 1:
                        self.keyboard_matrix._counts[event.code_name] += 1
                    self.keyboard_matrix._last_ns[event.code_name] = event.host_ns
                    self._pending_visual_update = True
                elif event.code_name.startswith("BTN_"):
                    self.mouse_view.buttons[event.code_name] = event.event_value
                    self._pending_visual_update = True

            elif event.type_name == "EV_REL":
                # Mouse / relative motion. Mutate state here; repaint only from the UI timer.
                if event.code_name == "REL_X":
                    self.mouse_view.dx_total += event.event_value
                    self.mouse_view.cursor += QPointF(event.event_value, 0)
                elif event.code_name == "REL_Y":
                    self.mouse_view.dy_total += event.event_value
                    self.mouse_view.cursor += QPointF(0, event.event_value)
                elif event.code_name in {"REL_WHEEL", "REL_WHEEL_HI_RES"}:
                    self.mouse_view.wheel_total += event.event_value
                elif event.code_name in {"REL_HWHEEL", "REL_HWHEEL_HI_RES"}:
                    self.mouse_view.hwheeel_total += event.event_value
                self.mouse_view.points.append(QPointF(self.mouse_view.cursor))
                self._pending_visual_update = True

            if not self.freeze_check.isChecked():
                if len(self._pending_log_events) < 1500:
                    self._pending_log_events.append(as_dict)
                else:
                    self._dropped_log_events += 1

    @Slot(str, str)
    def handle_device_error(self, path: str, message: str) -> None:
        self.status.setText(f"{path}: {message}")

    @Slot(str)
    def handle_device_stopped(self, path: str) -> None:
        self.status.setText(f"Stopped {path}")

    def event_passes_filter(self, event: dict[str, Any]) -> bool:
        tf = self.type_filter.currentText()
        if tf != "All":
            if tf == "Other":
                if event["type_name"] in {"EV_KEY", "EV_REL", "EV_ABS", "EV_MSC", "EV_LED", "EV_SYN"}:
                    return False
            elif event["type_name"] != tf:
                return False
        text = self.text_filter.text().strip().lower()
        if text:
            haystack = " ".join(str(event.get(k, "")) for k in ["device_name", "device_path", "type_name", "code_name", "event_value"]).lower()
            if text not in haystack:
                return False
        return True

    def append_log_row(self, event: dict[str, Any]) -> None:
        self.append_log_rows([event])

    def append_log_rows(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        filtered = [event for event in events if self.event_passes_filter(event)]
        if not filtered:
            return

        self.log_table.setUpdatesEnabled(False)
        try:
            overflow = max(0, self.log_table.rowCount() + len(filtered) - MAX_LOG_ROWS)
            for _ in range(overflow):
                self.log_table.removeRow(0)

            start_row = self.log_table.rowCount()
            self.log_table.setRowCount(start_row + len(filtered))
            base_index = max(0, len(self.raw_events) - len(events))

            for offset, event in enumerate(filtered):
                row = start_row + offset
                host_ms = event["host_monotonic_ns"] / 1_000_000.0
                raw = json.dumps(event, separators=(",", ":"))
                values = [
                    str(base_index + offset + 1),
                    f"{host_ms:.3f}",
                    event["device_name"],
                    event["device_path"],
                    event["type_name"],
                    event["code_name"],
                    str(event["event_value"]),
                    f"{event['kernel_time_sec']}.{event['kernel_time_usec']:06d}",
                    raw,
                ]
                for col, value in enumerate(values):
                    self.log_table.setItem(row, col, QTableWidgetItem(value))
        finally:
            self.log_table.setUpdatesEnabled(True)
        self.log_table.scrollToBottom()

    @Slot()
    def flush_log_batch(self) -> None:
        if self.freeze_check.isChecked() or not self._pending_log_events:
            return
        batch = self._pending_log_events
        self._pending_log_events = []
        self.append_log_rows(batch)

    @Slot()
    def rebuild_log_table(self) -> None:
        self.log_table.setRowCount(0)
        self.append_log_rows(list(self.raw_events))

    @Slot()
    def _refresh_ui(self) -> None:
        total = sum(s.total for s in self.stats.values())
        self.metric_total.setText(str(total))
        firsts = [s.first_ns for s in self.stats.values() if s.first_ns is not None]
        lasts = [s.last_ns for s in self.stats.values() if s.last_ns is not None]
        if firsts and lasts and max(lasts) > min(firsts):
            rate = total / ((max(lasts) - min(firsts)) / 1_000_000_000.0)
        else:
            rate = 0.0
        self.metric_rate.setText(f"{rate:.2f} Hz")
        if self.last_event_ns:
            self.metric_age.setText(f"{ns_to_ms(now_ns() - self.last_event_ns):.1f} ms")
        else:
            self.metric_age.setText("never")

        if self._pending_visual_update:
            self.keyboard_matrix.update()
            self.mouse_view.update()
            self._pending_visual_update = False
        self.metric_devices.setText(str(len(self.readers)))

        keys_down = [k.replace("KEY_", "") for k, v in sorted(self.keyboard_matrix._state.items()) if v]
        self.metric_keys_down.setText(", ".join(keys_down[:24]) if keys_down else "none")
        self.metric_mouse.setText(f"dx={self.mouse_view.dx_total} dy={self.mouse_view.dy_total} wheel={self.mouse_view.wheel_total} hwheel={self.mouse_view.hwheeel_total}")
        self.device_table.update_stats(self.stats)
        self.analysis_text.setPlainText(self.build_counter_report())

        # Additional system-level indicators
        system_keys = [k for k, v in self.keyboard_matrix._state.items() if v and ("POWER" in k or "SLEEP" in k or "WAKE" in k)]
        if system_keys:
            self.status.setText(f"System event active: {', '.join(system_keys)}")

    def build_counter_report(self) -> str:
        lines = []
        lines.append("GLOBAL")
        lines.append(f"  devices enumerated: {len(self.devices)}")
        lines.append(f"  devices capturing : {len(self.readers)}")
        lines.append(f"  retained log rows : {len(self.raw_events)} / {MAX_LOG_ROWS}")
        lines.append(f"  pending GUI rows  : {len(self._pending_log_events)}")
        lines.append(f"  dropped GUI rows  : {self._dropped_log_events}")
        lines.append("")
        for path in sorted(self.stats):
            st = self.stats[path]
            name = next((d.name for d in self.devices if d.path == path), path)
            lines.append(f"DEVICE {path}  {name}")
            lines.append(f"  total       : {st.total}")
            lines.append(f"  rate        : {st.rate_hz():.2f} Hz")
            lines.append(f"  last        : {st.last_event_text}")
            for type_name, count in sorted(st.by_type.items()):
                lines.append(f"  {type_name:<12}: {count}")
            lines.append("")
        if self.keyboard_matrix._counts:
            lines.append("KEY PRESS COUNTS")
            for key, count in sorted(self.keyboard_matrix._counts.items(), key=lambda kv: (-kv[1], kv[0]))[:200]:
                lines.append(f"  {key:<24} {count}")
            lines.append("")
        return "\n".join(lines)

    @Slot()
    def clear_state(self) -> None:
        self.stats.clear()
        self.raw_events.clear()
        self._pending_log_events.clear()
        self._dropped_log_events = 0
        self.last_event_ns = None
        self.log_table.setRowCount(0)
        self.keyboard_matrix.reset()
        self.mouse_view.reset()
        self._refresh_ui()

    @Slot()
    def export_jsonl(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export raw events", str(Path.home() / "input-lab-events.jsonl"), "JSON Lines (*.jsonl);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for event in self.raw_events:
                    f.write(json.dumps(event, sort_keys=True) + "\n")
            self.status.setText(f"Exported {len(self.raw_events)} events to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def closeEvent(self, event) -> None:
        try:
            if self.aggregator:
                # Ensure timer is stopped in the correct thread
                self.aggregator.stop()
            if self.agg_thread:
                self.agg_thread.quit()
                self.agg_thread.wait(500)
        except Exception:
            pass
        self.stop_capture()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Engineering Diagnostics")

    if EVDEV_IMPORT_ERROR is not None:
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Missing evdev")
        msg.setText("python-evdev is required for Linux input capture.")
        msg.setInformativeText(str(EVDEV_IMPORT_ERROR))
        msg.exec()
        return 2

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception:
        traceback.print_exc()
        raise
