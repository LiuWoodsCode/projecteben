#!/usr/bin/env python3
"""
Pi Stress Lab - PySide6 Raspberry Pi stress workload controller.

This tool focuses only on generating configurable stress workloads.
It intentionally does not diagnose, interpret, or report effects such as
thermal throttling, undervoltage, stability, clock changes, or temperature.

Install on Raspberry Pi OS:
    python3 -m venv .venv
    . .venv/bin/activate
    pip install PySide6 psutil

Run:
    python3 pi_stress_lab_pyside6.py

Optional packages:
    stress-ng may be installed separately, but this app does not require it.

Safety note:
    This is an expert tool. It can deliberately consume CPU, memory, disk I/O,
    and process table resources. Use disposable test directories for disk I/O.
"""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import queue
import random
import signal
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Pi Stress Lab"
ORG_NAME = "RaspberryPiEngineeringStyleTools"


# -----------------------------------------------------------------------------
# Workload configuration
# -----------------------------------------------------------------------------


@dataclass
class CpuStressConfig:
    enabled: bool = True
    workers: int = max(1, os.cpu_count() or 1)
    duty_cycle_percent: float = 100.0
    algorithm: str = "mixed_fp_int"
    matrix_size: int = 48
    prime_limit: int = 12000
    yield_interval_ms: int = 0
    cpu_affinity: str = ""  # Comma list, e.g. "0,1,2,3". Empty = inherit.


@dataclass
class MemoryStressConfig:
    enabled: bool = False
    workers: int = 1
    mib_per_worker: int = 128
    touch_stride_bytes: int = 4096
    pattern: str = "xor_walk"
    allocation_mode: str = "bytearray"
    churn_percent: float = 0.0


@dataclass
class DiskStressConfig:
    enabled: bool = False
    workers: int = 1
    directory: str = field(default_factory=lambda: str(Path(tempfile.gettempdir()) / "pi-stress-lab"))
    file_size_mib: int = 256
    block_size_kib: int = 1024
    mode: str = "write_read_verify"
    fsync_every_blocks: int = 0
    delete_on_stop: bool = True


@dataclass
class ProcessStressConfig:
    enabled: bool = False
    workers: int = 0
    child_sleep_ms: int = 250
    spawn_interval_ms: int = 100


@dataclass
class RampConfig:
    enabled: bool = False
    seconds: int = 60
    start_percent: float = 10.0
    end_percent: float = 100.0


@dataclass
class StressConfig:
    cpu: CpuStressConfig = field(default_factory=CpuStressConfig)
    memory: MemoryStressConfig = field(default_factory=MemoryStressConfig)
    disk: DiskStressConfig = field(default_factory=DiskStressConfig)
    process: ProcessStressConfig = field(default_factory=ProcessStressConfig)
    ramp: RampConfig = field(default_factory=RampConfig)
    run_label: str = "manual"
    max_runtime_seconds: int = 0  # 0 means unlimited.
    supervisor_poll_ms: int = 500


# -----------------------------------------------------------------------------
# Worker implementations
# -----------------------------------------------------------------------------


def parse_affinity(text: str) -> Optional[List[int]]:
    text = text.strip()
    if not text:
        return None
    cpus: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            cpus.extend(range(start, end + 1))
        else:
            cpus.append(int(part))
    return sorted(set(cpus))


def apply_affinity(affinity_text: str) -> None:
    cpus = parse_affinity(affinity_text)
    if cpus is None:
        return
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, cpus)


def should_run(stop_event: mp.Event) -> bool:
    return not stop_event.is_set()


def duty_cycle_wait(duty: float, active_started: float, period_s: float = 0.1) -> None:
    duty = max(0.0, min(100.0, duty))
    if duty >= 100.0:
        return
    if duty <= 0.0:
        time.sleep(period_s)
        return
    active_s = period_s * (duty / 100.0)
    elapsed = time.perf_counter() - active_started
    if elapsed >= active_s:
        time.sleep(max(0.0, period_s - elapsed))


def cpu_algorithm_mixed(matrix_size: int, prime_limit: int) -> float:
    # Mixed integer, floating point, memory-local work. Deliberately not NumPy:
    # this should stress regular Python/native math paths consistently anywhere.
    acc = 0.0

    # Floating point transcendental churn.
    for i in range(1, 5000):
        acc += math.sin(i * 0.001) * math.cos(i * 0.003)
        acc += math.sqrt((i % 97) + 1)

    # Integer/prime-like loop.
    count = 0
    for n in range(2, max(3, prime_limit)):
        root = int(math.sqrt(n))
        is_prime = True
        for d in range(2, root + 1):
            if n % d == 0:
                is_prime = False
                break
        if is_prime:
            count += 1

    # Small matrix multiplication using Python lists.
    size = max(8, min(128, matrix_size))
    a = [[(r * c + 1) % 17 for c in range(size)] for r in range(size)]
    b = [[(r + c + 3) % 19 for c in range(size)] for r in range(size)]
    total = 0
    for r in range(size):
        for c in range(size):
            cell = 0
            for k in range(size):
                cell += a[r][k] * b[k][c]
            total += cell

    return acc + count + total


def cpu_algorithm_integer(prime_limit: int) -> int:
    x = 0x123456789ABCDEF
    for i in range(max(10000, prime_limit * 12)):
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= (x >> 7)
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        x = (x + i * 2654435761) & 0xFFFFFFFFFFFFFFFF
    return x


def cpu_algorithm_float() -> float:
    x = 1.000001
    y = 0.999999
    for i in range(250000):
        x = math.sin(x + i * 0.000001) + math.cos(y)
        y = math.sqrt(abs(x) + 1.0) * 0.999999
    return x + y


def cpu_worker(index: int, cfg: CpuStressConfig, stop_event: mp.Event, event_q: mp.Queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        apply_affinity(cfg.cpu_affinity)
    except Exception as exc:
        event_q.put(("warn", f"CPU worker {index}: affinity not applied: {exc}"))

    iterations = 0
    last_report = time.monotonic()
    event_q.put(("info", f"CPU worker {index} started: algorithm={cfg.algorithm}"))

    while should_run(stop_event):
        start = time.perf_counter()
        if cfg.algorithm == "integer_xorshift":
            cpu_algorithm_integer(cfg.prime_limit)
        elif cfg.algorithm == "float_transcendental":
            cpu_algorithm_float()
        else:
            cpu_algorithm_mixed(cfg.matrix_size, cfg.prime_limit)
        iterations += 1
        duty_cycle_wait(cfg.duty_cycle_percent, start)

        if cfg.yield_interval_ms > 0:
            time.sleep(cfg.yield_interval_ms / 1000.0)

        now = time.monotonic()
        if now - last_report >= 2.0:
            event_q.put(("metric", f"CPU worker {index}: iterations={iterations}"))
            last_report = now

    event_q.put(("info", f"CPU worker {index} stopped: iterations={iterations}"))


def memory_worker(index: int, cfg: MemoryStressConfig, stop_event: mp.Event, event_q: mp.Queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    size = max(1, cfg.mib_per_worker) * 1024 * 1024
    stride = max(64, cfg.touch_stride_bytes)
    rng = random.Random(index + int(time.time()))

    event_q.put(("info", f"Memory worker {index} allocating {cfg.mib_per_worker} MiB"))

    if cfg.allocation_mode == "list_ints":
        cells = max(1, size // 8)
        mem: Any = [0] * cells
    else:
        mem = bytearray(size)

    loops = 0
    last_report = time.monotonic()

    while should_run(stop_event):
        if isinstance(mem, bytearray):
            if cfg.pattern == "random_touch":
                for _ in range(max(1, size // stride)):
                    pos = rng.randrange(0, size)
                    mem[pos] = (mem[pos] + 1) & 0xFF
            elif cfg.pattern == "fill_invert":
                fill = loops & 0xFF
                mem[:] = bytes([fill]) * len(mem)
                for pos in range(0, size, stride):
                    mem[pos] ^= 0xFF
            else:
                x = loops & 0xFF
                for pos in range(0, size, stride):
                    x = ((x << 1) ^ pos ^ 0xA5) & 0xFF
                    mem[pos] = x
        else:
            for pos in range(0, len(mem), max(1, stride // 8)):
                mem[pos] = (mem[pos] + pos + loops) & 0xFFFFFFFF

        if cfg.churn_percent > 0.0 and rng.random() < (cfg.churn_percent / 100.0):
            if isinstance(mem, bytearray):
                mem = bytearray(size)
            else:
                mem = [0] * max(1, size // 8)

        loops += 1
        now = time.monotonic()
        if now - last_report >= 2.0:
            event_q.put(("metric", f"Memory worker {index}: loops={loops}, allocation={cfg.mib_per_worker} MiB"))
            last_report = now

    event_q.put(("info", f"Memory worker {index} stopped"))


def disk_worker(index: int, cfg: DiskStressConfig, stop_event: mp.Event, event_q: mp.Queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    directory = Path(cfg.directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"pi-stress-lab-worker-{index}-{os.getpid()}.bin"

    file_size = max(1, cfg.file_size_mib) * 1024 * 1024
    block_size = max(4, cfg.block_size_kib) * 1024
    block = os.urandom(block_size)
    loops = 0
    last_report = time.monotonic()

    event_q.put(("info", f"Disk worker {index} started: path={path}, mode={cfg.mode}"))

    try:
        while should_run(stop_event):
            if cfg.mode in ("write_only", "write_read_verify", "random_rewrite"):
                with open(path, "wb", buffering=0) as f:
                    written = 0
                    block_index = 0
                    while written < file_size and should_run(stop_event):
                        f.write(block[: min(block_size, file_size - written)])
                        written += block_size
                        block_index += 1
                        if cfg.fsync_every_blocks > 0 and block_index % cfg.fsync_every_blocks == 0:
                            os.fsync(f.fileno())
                    if cfg.fsync_every_blocks > 0:
                        os.fsync(f.fileno())

            if cfg.mode == "random_rewrite" and path.exists():
                with open(path, "r+b", buffering=0) as f:
                    for _ in range(max(1, file_size // block_size)):
                        if not should_run(stop_event):
                            break
                        offset = random.randrange(0, max(1, file_size - block_size + 1), block_size)
                        f.seek(offset)
                        f.write(block)

            if cfg.mode in ("read_only", "write_read_verify") and path.exists():
                if cfg.mode == "read_only" and not path.exists():
                    path.write_bytes(block)
                checksum = 0
                with open(path, "rb", buffering=0) as f:
                    while should_run(stop_event):
                        data = f.read(block_size)
                        if not data:
                            break
                        checksum ^= data[0]

            loops += 1
            now = time.monotonic()
            if now - last_report >= 2.0:
                event_q.put(("metric", f"Disk worker {index}: loops={loops}, file={path.name}"))
                last_report = now
    finally:
        if cfg.delete_on_stop:
            try:
                path.unlink(missing_ok=True)
            except Exception as exc:
                event_q.put(("warn", f"Disk worker {index}: could not delete {path}: {exc}"))
        event_q.put(("info", f"Disk worker {index} stopped"))


def tiny_child_sleep(ms: int) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    time.sleep(max(1, ms) / 1000.0)


def process_worker(index: int, cfg: ProcessStressConfig, stop_event: mp.Event, event_q: mp.Queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    spawned = 0
    active: List[mp.Process] = []
    last_report = time.monotonic()
    event_q.put(("info", f"Process worker {index} started"))

    while should_run(stop_event):
        active = [p for p in active if p.is_alive()]
        p = mp.Process(target=tiny_child_sleep, args=(cfg.child_sleep_ms,), daemon=True)
        p.start()
        active.append(p)
        spawned += 1
        time.sleep(max(1, cfg.spawn_interval_ms) / 1000.0)

        now = time.monotonic()
        if now - last_report >= 2.0:
            event_q.put(("metric", f"Process worker {index}: spawned={spawned}, active={len(active)}"))
            last_report = now

    for p in active:
        p.join(timeout=0.2)
    event_q.put(("info", f"Process worker {index} stopped: spawned={spawned}"))


# -----------------------------------------------------------------------------
# Supervisor
# -----------------------------------------------------------------------------


class StressSupervisor:
    def __init__(self) -> None:
        self.stop_event: Optional[mp.Event] = None
        self.event_q: Optional[mp.Queue] = None
        self.processes: List[mp.Process] = []
        self.started_at: Optional[float] = None
        self.config: Optional[StressConfig] = None

    def is_running(self) -> bool:
        return bool(self.processes)

    def start(self, cfg: StressConfig) -> None:
        if self.is_running():
            raise RuntimeError("stress run already active")

        self.config = cfg
        self.stop_event = mp.Event()
        self.event_q = mp.Queue()
        self.processes = []
        self.started_at = time.monotonic()

        def add_many(prefix: str, count: int, target: Any, subcfg: Any) -> None:
            for i in range(max(0, count)):
                p = mp.Process(target=target, args=(i, subcfg, self.stop_event, self.event_q), daemon=True, name=f"{prefix}-{i}")
                p.start()
                self.processes.append(p)

        if cfg.cpu.enabled:
            add_many("cpu", cfg.cpu.workers, cpu_worker, cfg.cpu)
        if cfg.memory.enabled:
            add_many("memory", cfg.memory.workers, memory_worker, cfg.memory)
        if cfg.disk.enabled:
            add_many("disk", cfg.disk.workers, disk_worker, cfg.disk)
        if cfg.process.enabled:
            add_many("process", cfg.process.workers, process_worker, cfg.process)

        if self.event_q is not None:
            self.event_q.put(("info", f"Run started: label={cfg.run_label}, processes={len(self.processes)}"))

    def stop(self) -> None:
        if not self.is_running():
            return
        assert self.stop_event is not None
        self.stop_event.set()
        deadline = time.monotonic() + 5.0
        for p in self.processes:
            remaining = max(0.0, deadline - time.monotonic())
            p.join(timeout=remaining)
        for p in self.processes:
            if p.is_alive():
                p.terminate()
        for p in self.processes:
            p.join(timeout=0.5)
        self.processes.clear()
        if self.event_q is not None:
            try:
                self.event_q.put(("info", "Run stopped"))
            except Exception:
                pass

    def poll_events(self) -> List[tuple[str, str]]:
        events: List[tuple[str, str]] = []
        if self.event_q is None:
            return events
        while True:
            try:
                events.append(self.event_q.get_nowait())
            except queue.Empty:
                break
            except Exception:
                break
        return events

    def reap_dead(self) -> List[str]:
        messages: List[str] = []
        alive: List[mp.Process] = []
        for p in self.processes:
            if p.is_alive():
                alive.append(p)
            else:
                p.join(timeout=0)
                messages.append(f"Worker exited: {p.name}, exitcode={p.exitcode}")
        self.processes = alive
        return messages

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at


# -----------------------------------------------------------------------------
# GUI
# -----------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 820)
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.supervisor = StressSupervisor()

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_run_tab()
        self._build_cpu_tab()
        self._build_memory_tab()
        self._build_disk_tab()
        self._build_process_tab()
        self._build_profiles_tab()
        self._build_log_tab()
        self._build_menu()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(500)

        self._load_settings()
        self._append_log("ready", "Pi Stress Lab initialized")

    # ----- construction helpers ------------------------------------------------

    def _spin(self, minimum: int, maximum: int, value: int, suffix: str = "") -> QSpinBox:
        s = QSpinBox()
        s.setRange(minimum, maximum)
        s.setValue(value)
        s.setSuffix(suffix)
        return s

    def _dspin(self, minimum: float, maximum: float, value: float, suffix: str = "") -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(minimum, maximum)
        s.setValue(value)
        s.setDecimals(2)
        s.setSuffix(suffix)
        return s

    def _combo(self, values: List[str], current: str) -> QComboBox:
        c = QComboBox()
        c.addItems(values)
        ix = c.findText(current)
        if ix >= 0:
            c.setCurrentIndex(ix)
        return c

    def _group(self, title: str, layout: QFormLayout) -> QGroupBox:
        g = QGroupBox(title)
        g.setLayout(layout)
        return g

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        save_action = QAction("Save profile...", self)
        save_action.triggered.connect(self._save_profile)
        load_action = QAction("Load profile...", self)
        load_action.triggered.connect(self._load_profile)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(save_action)
        file_menu.addAction(load_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

    def _build_run_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)

        form = QFormLayout()
        self.run_label = QLineEdit("manual")
        self.max_runtime = self._spin(0, 86400, 0, " s")
        self.poll_ms = self._spin(100, 5000, 500, " ms")
        self.ramp_enabled = QCheckBox("Enable workload ramp")
        self.ramp_seconds = self._spin(1, 86400, 60, " s")
        self.ramp_start = self._dspin(0.0, 100.0, 10.0, " %")
        self.ramp_end = self._dspin(0.0, 100.0, 100.0, " %")

        form.addRow("Run label", self.run_label)
        form.addRow("Maximum runtime", self.max_runtime)
        form.addRow("Supervisor poll interval", self.poll_ms)
        form.addRow("Ramp", self.ramp_enabled)
        form.addRow("Ramp duration", self.ramp_seconds)
        form.addRow("Ramp start duty", self.ramp_start)
        form.addRow("Ramp end duty", self.ramp_end)
        outer.addWidget(self._group("Run Control Plane", form))

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start Stress Run")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        outer.addLayout(buttons)

        self.status_label = QLabel("Idle")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.elapsed_bar = QProgressBar()
        self.elapsed_bar.setRange(0, 1000)
        self.elapsed_bar.setValue(0)
        outer.addWidget(self.status_label)
        outer.addWidget(self.elapsed_bar)
        outer.addStretch(1)
        self.tabs.addTab(page, "Run")

    def _build_cpu_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.cpu_enabled = QCheckBox("Enable CPU stress")
        self.cpu_enabled.setChecked(True)
        self.cpu_workers = self._spin(0, 256, max(1, os.cpu_count() or 1))
        self.cpu_duty = self._dspin(0.0, 100.0, 100.0, " %")
        self.cpu_algorithm = self._combo(["mixed_fp_int", "integer_xorshift", "float_transcendental"], "mixed_fp_int")
        self.cpu_matrix = self._spin(8, 128, 48)
        self.cpu_prime = self._spin(100, 200000, 12000)
        self.cpu_yield = self._spin(0, 1000, 0, " ms")
        self.cpu_affinity = QLineEdit("")
        self.cpu_affinity.setPlaceholderText("e.g. 0-3 or 0,2,3; empty = inherit")
        form.addRow("Enabled", self.cpu_enabled)
        form.addRow("Workers", self.cpu_workers)
        form.addRow("Duty cycle", self.cpu_duty)
        form.addRow("Algorithm", self.cpu_algorithm)
        form.addRow("Matrix size", self.cpu_matrix)
        form.addRow("Prime limit", self.cpu_prime)
        form.addRow("Yield interval", self.cpu_yield)
        form.addRow("CPU affinity", self.cpu_affinity)
        self.tabs.addTab(page, "CPU")

    def _build_memory_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.mem_enabled = QCheckBox("Enable memory stress")
        self.mem_workers = self._spin(0, 64, 1)
        self.mem_mib = self._spin(1, 65536, 128, " MiB")
        self.mem_stride = self._spin(64, 16 * 1024 * 1024, 4096, " bytes")
        self.mem_pattern = self._combo(["xor_walk", "random_touch", "fill_invert"], "xor_walk")
        self.mem_alloc = self._combo(["bytearray", "list_ints"], "bytearray")
        self.mem_churn = self._dspin(0.0, 100.0, 0.0, " %")
        form.addRow("Enabled", self.mem_enabled)
        form.addRow("Workers", self.mem_workers)
        form.addRow("MiB per worker", self.mem_mib)
        form.addRow("Touch stride", self.mem_stride)
        form.addRow("Pattern", self.mem_pattern)
        form.addRow("Allocation mode", self.mem_alloc)
        form.addRow("Churn probability", self.mem_churn)
        self.tabs.addTab(page, "Memory")

    def _build_disk_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.disk_enabled = QCheckBox("Enable disk I/O stress")
        self.disk_workers = self._spin(0, 32, 1)
        self.disk_dir = QLineEdit(str(Path(tempfile.gettempdir()) / "pi-stress-lab"))
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse_disk_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.disk_dir)
        dir_row.addWidget(browse)
        dir_widget = QWidget()
        dir_widget.setLayout(dir_row)
        self.disk_file_mib = self._spin(1, 1024 * 1024, 256, " MiB")
        self.disk_block_kib = self._spin(4, 1024 * 1024, 1024, " KiB")
        self.disk_mode = self._combo(["write_read_verify", "write_only", "read_only", "random_rewrite"], "write_read_verify")
        self.disk_fsync = self._spin(0, 1000000, 0, " blocks")
        self.disk_delete = QCheckBox("Delete worker files on stop")
        self.disk_delete.setChecked(True)
        form.addRow("Enabled", self.disk_enabled)
        form.addRow("Workers", self.disk_workers)
        form.addRow("Directory", dir_widget)
        form.addRow("File size", self.disk_file_mib)
        form.addRow("Block size", self.disk_block_kib)
        form.addRow("Mode", self.disk_mode)
        form.addRow("fsync every", self.disk_fsync)
        form.addRow("Cleanup", self.disk_delete)
        self.tabs.addTab(page, "Disk I/O")

    def _build_process_tab(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.proc_enabled = QCheckBox("Enable process churn stress")
        self.proc_workers = self._spin(0, 32, 0)
        self.proc_sleep = self._spin(1, 60000, 250, " ms")
        self.proc_spawn = self._spin(1, 60000, 100, " ms")
        form.addRow("Enabled", self.proc_enabled)
        form.addRow("Spawner workers", self.proc_workers)
        form.addRow("Child lifetime", self.proc_sleep)
        form.addRow("Spawn interval", self.proc_spawn)
        self.tabs.addTab(page, "Process")

    def _build_profiles_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        self.profile_text = QPlainTextEdit()
        self.profile_text.setFont(QFont("monospace"))
        refresh = QPushButton("Generate JSON From Current Configuration")
        apply = QPushButton("Apply JSON To Controls")
        refresh.clicked.connect(self._refresh_profile_text)
        apply.clicked.connect(self._apply_profile_text)
        outer.addWidget(refresh)
        outer.addWidget(apply)
        outer.addWidget(self.profile_text)
        self.tabs.addTab(page, "Profile JSON")

    def _build_log_tab(self) -> None:
        page = QWidget()
        outer = QVBoxLayout(page)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setFont(QFont("monospace"))
        clear = QPushButton("Clear Log")
        clear.clicked.connect(self.log.clear)
        outer.addWidget(clear)
        outer.addWidget(self.log)
        self.tabs.addTab(page, "Internal Log")

    # ----- config mapping ------------------------------------------------------

    def _config_from_ui(self) -> StressConfig:
        return StressConfig(
            cpu=CpuStressConfig(
                enabled=self.cpu_enabled.isChecked(),
                workers=self.cpu_workers.value(),
                duty_cycle_percent=self.cpu_duty.value(),
                algorithm=self.cpu_algorithm.currentText(),
                matrix_size=self.cpu_matrix.value(),
                prime_limit=self.cpu_prime.value(),
                yield_interval_ms=self.cpu_yield.value(),
                cpu_affinity=self.cpu_affinity.text(),
            ),
            memory=MemoryStressConfig(
                enabled=self.mem_enabled.isChecked(),
                workers=self.mem_workers.value(),
                mib_per_worker=self.mem_mib.value(),
                touch_stride_bytes=self.mem_stride.value(),
                pattern=self.mem_pattern.currentText(),
                allocation_mode=self.mem_alloc.currentText(),
                churn_percent=self.mem_churn.value(),
            ),
            disk=DiskStressConfig(
                enabled=self.disk_enabled.isChecked(),
                workers=self.disk_workers.value(),
                directory=self.disk_dir.text(),
                file_size_mib=self.disk_file_mib.value(),
                block_size_kib=self.disk_block_kib.value(),
                mode=self.disk_mode.currentText(),
                fsync_every_blocks=self.disk_fsync.value(),
                delete_on_stop=self.disk_delete.isChecked(),
            ),
            process=ProcessStressConfig(
                enabled=self.proc_enabled.isChecked(),
                workers=self.proc_workers.value(),
                child_sleep_ms=self.proc_sleep.value(),
                spawn_interval_ms=self.proc_spawn.value(),
            ),
            ramp=RampConfig(
                enabled=self.ramp_enabled.isChecked(),
                seconds=self.ramp_seconds.value(),
                start_percent=self.ramp_start.value(),
                end_percent=self.ramp_end.value(),
            ),
            run_label=self.run_label.text().strip() or "manual",
            max_runtime_seconds=self.max_runtime.value(),
            supervisor_poll_ms=self.poll_ms.value(),
        )

    def _apply_config(self, cfg: StressConfig) -> None:
        self.run_label.setText(cfg.run_label)
        self.max_runtime.setValue(cfg.max_runtime_seconds)
        self.poll_ms.setValue(cfg.supervisor_poll_ms)
        self.ramp_enabled.setChecked(cfg.ramp.enabled)
        self.ramp_seconds.setValue(cfg.ramp.seconds)
        self.ramp_start.setValue(cfg.ramp.start_percent)
        self.ramp_end.setValue(cfg.ramp.end_percent)

        self.cpu_enabled.setChecked(cfg.cpu.enabled)
        self.cpu_workers.setValue(cfg.cpu.workers)
        self.cpu_duty.setValue(cfg.cpu.duty_cycle_percent)
        self.cpu_algorithm.setCurrentText(cfg.cpu.algorithm)
        self.cpu_matrix.setValue(cfg.cpu.matrix_size)
        self.cpu_prime.setValue(cfg.cpu.prime_limit)
        self.cpu_yield.setValue(cfg.cpu.yield_interval_ms)
        self.cpu_affinity.setText(cfg.cpu.cpu_affinity)

        self.mem_enabled.setChecked(cfg.memory.enabled)
        self.mem_workers.setValue(cfg.memory.workers)
        self.mem_mib.setValue(cfg.memory.mib_per_worker)
        self.mem_stride.setValue(cfg.memory.touch_stride_bytes)
        self.mem_pattern.setCurrentText(cfg.memory.pattern)
        self.mem_alloc.setCurrentText(cfg.memory.allocation_mode)
        self.mem_churn.setValue(cfg.memory.churn_percent)

        self.disk_enabled.setChecked(cfg.disk.enabled)
        self.disk_workers.setValue(cfg.disk.workers)
        self.disk_dir.setText(cfg.disk.directory)
        self.disk_file_mib.setValue(cfg.disk.file_size_mib)
        self.disk_block_kib.setValue(cfg.disk.block_size_kib)
        self.disk_mode.setCurrentText(cfg.disk.mode)
        self.disk_fsync.setValue(cfg.disk.fsync_every_blocks)
        self.disk_delete.setChecked(cfg.disk.delete_on_stop)

        self.proc_enabled.setChecked(cfg.process.enabled)
        self.proc_workers.setValue(cfg.process.workers)
        self.proc_sleep.setValue(cfg.process.child_sleep_ms)
        self.proc_spawn.setValue(cfg.process.spawn_interval_ms)

    def _dict_to_config(self, data: Dict[str, Any]) -> StressConfig:
        return StressConfig(
            cpu=CpuStressConfig(**data.get("cpu", {})),
            memory=MemoryStressConfig(**data.get("memory", {})),
            disk=DiskStressConfig(**data.get("disk", {})),
            process=ProcessStressConfig(**data.get("process", {})),
            ramp=RampConfig(**data.get("ramp", {})),
            run_label=data.get("run_label", "manual"),
            max_runtime_seconds=int(data.get("max_runtime_seconds", 0)),
            supervisor_poll_ms=int(data.get("supervisor_poll_ms", 500)),
        )

    # ----- actions -------------------------------------------------------------

    def _validate_config(self, cfg: StressConfig) -> None:
        enabled_workers = 0
        if cfg.cpu.enabled:
            enabled_workers += cfg.cpu.workers
            parse_affinity(cfg.cpu.cpu_affinity)
        if cfg.memory.enabled:
            enabled_workers += cfg.memory.workers
        if cfg.disk.enabled:
            enabled_workers += cfg.disk.workers
            directory = Path(cfg.disk.directory)
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
            if not os.access(directory, os.W_OK):
                raise ValueError(f"Disk directory is not writable: {directory}")
        if cfg.process.enabled:
            enabled_workers += cfg.process.workers
        if enabled_workers <= 0:
            raise ValueError("No enabled workers configured")

    def _start(self) -> None:
        cfg = self._config_from_ui()
        try:
            self._validate_config(cfg)
            self.supervisor.start(cfg)
        except Exception as exc:
            QMessageBox.critical(self, "Cannot start stress run", str(exc))
            self._append_log("error", f"start rejected: {exc}")
            return

        self.timer.setInterval(cfg.supervisor_poll_ms)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._append_log("info", "stress run launched")

    def _stop(self) -> None:
        self.supervisor.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.elapsed_bar.setValue(0)
        self.status_label.setText("Idle")
        self._append_log("info", "stress run stopped by user")

    def _tick(self) -> None:
        for level, msg in self.supervisor.poll_events():
            self._append_log(level, msg)

        for msg in self.supervisor.reap_dead():
            self._append_log("warn", msg)

        if self.supervisor.is_running():
            elapsed = self.supervisor.elapsed()
            cfg = self.supervisor.config
            assert cfg is not None

            if cfg.max_runtime_seconds > 0 and elapsed >= cfg.max_runtime_seconds:
                self._append_log("info", "maximum runtime reached")
                self._stop()
                return

            if cfg.max_runtime_seconds > 0:
                self.elapsed_bar.setValue(min(1000, int((elapsed / cfg.max_runtime_seconds) * 1000)))
            else:
                self.elapsed_bar.setValue((self.elapsed_bar.value() + 7) % 1000)

            live = len(self.supervisor.processes)
            self.status_label.setText(f"Running | elapsed={elapsed:.1f}s | live workers={live}")
        else:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

    def _refresh_profile_text(self) -> None:
        self.profile_text.setPlainText(json.dumps(asdict(self._config_from_ui()), indent=2))

    def _apply_profile_text(self) -> None:
        try:
            data = json.loads(self.profile_text.toPlainText())
            cfg = self._dict_to_config(data)
            self._apply_config(cfg)
            self._append_log("info", "profile JSON applied")
        except Exception as exc:
            QMessageBox.critical(self, "Invalid profile JSON", str(exc))

    def _save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", "pi-stress-profile.json", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(asdict(self._config_from_ui()), indent=2), encoding="utf-8")
        self._append_log("info", f"profile saved: {path}")

    def _load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load profile", "", "JSON (*.json)")
        if not path:
            return
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self._apply_config(self._dict_to_config(data))
        self._append_log("info", f"profile loaded: {path}")

    def _browse_disk_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select disk stress directory", self.disk_dir.text())
        if path:
            self.disk_dir.setText(path)

    def _append_log(self, level: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{ts}] {level.upper():>6} | {msg}")

    # ----- settings ------------------------------------------------------------

    def _load_settings(self) -> None:
        raw = self.settings.value("config_json", "")
        if raw:
            try:
                self._apply_config(self._dict_to_config(json.loads(str(raw))))
            except Exception:
                pass

    def _save_settings(self) -> None:
        self.settings.setValue("config_json", json.dumps(asdict(self._config_from_ui())))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.supervisor.is_running():
            reply = QMessageBox.question(
                self,
                "Stress run active",
                "A stress run is active. Stop it and quit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.supervisor.stop()
        self._save_settings()
        event.accept()


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def main() -> int:
    mp.set_start_method("spawn", force=True)
    app = QApplication([])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
