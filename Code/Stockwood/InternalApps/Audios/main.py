#!/usr/bin/env python3
"""
Pi Audio Lab - PySide6 engineering console for Raspberry Pi OS audio work.

Scope:
  - Audio only. No general CPU/RAM/network/system information panels.
  - Uses CLI tools commonly available in Raspberry Pi OS / Debian apt packages.
  - Designed for expert/internal diagnostics rather than consumer simplicity.

Recommended packages:
  sudo apt update
  sudo apt install -y python3-pyside6.qtwidgets python3-pyside6.qtcore \
      alsa-utils pulseaudio-utils pipewire-audio-client-libraries \
      libasound2-plugins sox ffmpeg lsof procps

Optional but useful:
  sudo apt install -y jackd2 qjackctl pavucontrol pipewire-bin wireplumber

Run:
  python3 pi_audio_lab_pyside6.py

Notes:
  - Some tools may be absent depending on whether the system uses ALSA-only,
    PulseAudio, PipeWire, or JACK.
  - The app degrades gracefully and shows missing-tool diagnostics.
  - Long-running tests are launched as managed jobs with live stdout/stderr.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont, QTextCursor
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
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Pi Audio Lab"
VERSION = "1.0.0"


# ----------------------------- utility layer -----------------------------

@dataclass
class CmdSpec:
    title: str
    argv: list[str]
    timeout_ms: int = 15_000
    stdin_text: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class CmdResult:
    title: str
    argv: list[str]
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False
    missing: bool = False
    started_at: _dt.datetime = field(default_factory=_dt.datetime.now)
    ended_at: _dt.datetime = field(default_factory=_dt.datetime.now)

    @property
    def elapsed_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def which(cmd: str) -> Optional[str]:
    from shutil import which as _which
    return _which(cmd)


def have(cmd: str) -> bool:
    return which(cmd) is not None


def shell_join(argv: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in argv)


def run_cmd(spec: CmdSpec) -> CmdResult:
    started = _dt.datetime.now()
    exe = spec.argv[0]
    if not have(exe):
        return CmdResult(
            title=spec.title,
            argv=spec.argv,
            returncode=None,
            stdout="",
            stderr=f"Missing required CLI tool: {exe}\nInstall with apt where available.",
            missing=True,
            started_at=started,
            ended_at=_dt.datetime.now(),
        )

    env = os.environ.copy()
    env.update(spec.env)
    try:
        cp = subprocess.run(
            spec.argv,
            input=spec.stdin_text,
            text=True,
            capture_output=True,
            timeout=spec.timeout_ms / 1000,
            env=env,
        )
        return CmdResult(
            title=spec.title,
            argv=spec.argv,
            returncode=cp.returncode,
            stdout=cp.stdout,
            stderr=cp.stderr,
            started_at=started,
            ended_at=_dt.datetime.now(),
        )
    except subprocess.TimeoutExpired as e:
        return CmdResult(
            title=spec.title,
            argv=spec.argv,
            returncode=None,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + f"\nTimed out after {spec.timeout_ms} ms.",
            timed_out=True,
            started_at=started,
            ended_at=_dt.datetime.now(),
        )
    except Exception as e:
        return CmdResult(
            title=spec.title,
            argv=spec.argv,
            returncode=None,
            stdout="",
            stderr=f"Exception while running command: {type(e).__name__}: {e}",
            started_at=started,
            ended_at=_dt.datetime.now(),
        )


def read_text_file(path: str, max_chars: int = 200_000) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return f"{path}: does not exist\n"
        if not p.is_file():
            return f"{path}: not a regular file\n"
        return p.read_text(errors="replace")[:max_chars]
    except Exception as e:
        return f"{path}: {type(e).__name__}: {e}\n"


def append_block(editor: QPlainTextEdit, title: str, body: str) -> None:
    editor.appendPlainText(f"\n{'=' * 100}\n[{now_stamp()}] {title}\n{'=' * 100}\n{body.rstrip()}\n")
    editor.moveCursor(QTextCursor.End)


def result_to_text(r: CmdResult) -> str:
    status = "missing" if r.missing else "timeout" if r.timed_out else str(r.returncode)
    out = [
        f"title      : {r.title}",
        f"command    : {shell_join(r.argv)}",
        f"returncode : {status}",
        f"elapsed    : {r.elapsed_ms} ms",
        "",
        "--- stdout ---",
        r.stdout.rstrip() or "<empty>",
        "",
        "--- stderr ---",
        r.stderr.rstrip() or "<empty>",
    ]
    return "\n".join(out)


# ----------------------------- parsers ------------------------------------

_CARD_RE = re.compile(r"^card\s+(?P<card>\d+):\s+(?P<id>[^\[]+)\[(?P<name>[^\]]+)\],\s+device\s+(?P<dev>\d+):\s+(?P<desc>.+)$")
_SUB_RE = re.compile(r"^\s+Subdevices:\s+(?P<avail>\d+)/(?:\d+)")


@dataclass
class AlsaDevice:
    kind: str
    card: int
    device: int
    id: str
    name: str
    description: str
    hw: str
    available_subdevices: Optional[int] = None


def parse_aplay_l(text: str, kind: str) -> list[AlsaDevice]:
    devices: list[AlsaDevice] = []
    current: Optional[AlsaDevice] = None
    for line in text.splitlines():
        m = _CARD_RE.match(line)
        if m:
            current = AlsaDevice(
                kind=kind,
                card=int(m.group("card")),
                device=int(m.group("dev")),
                id=m.group("id").strip(),
                name=m.group("name").strip(),
                description=m.group("desc").strip(),
                hw=f"hw:{m.group('card')},{m.group('dev')}",
            )
            devices.append(current)
            continue
        sm = _SUB_RE.match(line)
        if sm and current:
            current.available_subdevices = int(sm.group("avail"))
    return devices


def parse_proc_asound_cards(text: str) -> list[tuple[str, str]]:
    cards: list[tuple[str, str]] = []
    for line in text.splitlines():
        if line.strip().startswith("---"):
            continue
        m = re.match(r"\s*(\d+)\s+\[([^\]]+)\s*\]:\s*(.*)$", line)
        if m:
            cards.append((m.group(1), f"{m.group(2).strip()} - {m.group(3).strip()}"))
    return cards


# ----------------------------- workers ------------------------------------

class SnapshotWorker(QObject):
    completed = Signal(str)

    @Slot()
    def run(self) -> None:
        specs = audio_snapshot_specs()
        chunks: list[str] = []
        chunks.append(f"{APP_NAME} snapshot at {now_stamp()}\n")
        chunks.append("Audio CLI availability\n----------------------")
        for tool in sorted(required_and_optional_tools()):
            chunks.append(f"{tool:18} : {which(tool) or '<missing>'}")
        chunks.append("")

        for spec in specs:
            r = run_cmd(spec)
            chunks.append(result_to_text(r))
            chunks.append("\n")

        chunks.append("Selected /proc/asound files\n---------------------------")
        for path in [
            "/proc/asound/cards",
            "/proc/asound/devices",
            "/proc/asound/modules",
            "/proc/asound/pcm",
            "/proc/asound/timers",
            "/proc/asound/version",
        ]:
            chunks.append(f"\n--- {path} ---\n{read_text_file(path)}")

        self.completed.emit("\n".join(chunks))


# ----------------------------- command model ------------------------------

def required_and_optional_tools() -> set[str]:
    return {
        "aplay", "arecord", "amixer", "alsactl", "speaker-test", "alsamixer",
        "pactl", "pacmd", "pw-cli", "pw-dump", "pw-top", "wpctl",
        "pipewire", "wireplumber", "jack_lsp", "jack_bufsize", "jack_samplerate",
        "sox", "play", "rec", "ffmpeg", "ffprobe", "fuser", "lsof", "ps",
    }


def audio_snapshot_specs() -> list[CmdSpec]:
    specs = [
        CmdSpec("ALSA playback devices: aplay -l", ["aplay", "-l"]),
        CmdSpec("ALSA capture devices: arecord -l", ["arecord", "-l"]),
        CmdSpec("ALSA PCM names: aplay -L", ["aplay", "-L"]),
        CmdSpec("ALSA capture PCM names: arecord -L", ["arecord", "-L"]),
        CmdSpec("ALSA mixer simple controls: amixer scontrols", ["amixer", "scontrols"]),
        CmdSpec("ALSA mixer full dump: amixer contents", ["amixer", "contents"], timeout_ms=20_000),
        CmdSpec("ALSA state dump: alsactl dump-state", ["alsactl", "dump-state"], timeout_ms=20_000),
        CmdSpec("Pulse/PipeWire server info: pactl info", ["pactl", "info"]),
        CmdSpec("Pulse/PipeWire sinks: pactl list sinks", ["pactl", "list", "sinks"], timeout_ms=20_000),
        CmdSpec("Pulse/PipeWire sources: pactl list sources", ["pactl", "list", "sources"], timeout_ms=20_000),
        CmdSpec("Pulse/PipeWire cards: pactl list cards", ["pactl", "list", "cards"], timeout_ms=20_000),
        CmdSpec("Pulse/PipeWire clients: pactl list clients", ["pactl", "list", "clients"], timeout_ms=20_000),
        CmdSpec("Pulse/PipeWire modules: pactl list modules", ["pactl", "list", "modules"], timeout_ms=20_000),
        CmdSpec("WirePlumber status: wpctl status", ["wpctl", "status"], timeout_ms=20_000),
        CmdSpec("PipeWire dump: pw-dump", ["pw-dump"], timeout_ms=25_000),
        CmdSpec("PipeWire objects: pw-cli ls", ["pw-cli", "ls"], timeout_ms=20_000),
        CmdSpec("JACK ports: jack_lsp -Acl", ["jack_lsp", "-Acl"], timeout_ms=10_000),
        CmdSpec("Processes using ALSA device nodes: fuser -v /dev/snd/*", ["fuser", "-v", "/dev/snd/*"], timeout_ms=10_000),
        CmdSpec("Open files under /dev/snd: lsof /dev/snd", ["lsof", "/dev/snd"], timeout_ms=10_000),
    ]
    return specs


def get_playback_devices() -> list[AlsaDevice]:
    r = run_cmd(CmdSpec("aplay -l", ["aplay", "-l"]))
    return parse_aplay_l(r.stdout, "playback") if not r.missing else []


def get_capture_devices() -> list[AlsaDevice]:
    r = run_cmd(CmdSpec("arecord -l", ["arecord", "-l"]))
    return parse_aplay_l(r.stdout, "capture") if not r.missing else []


def get_pactl_short(kind: str) -> list[str]:
    r = run_cmd(CmdSpec(f"pactl list short {kind}", ["pactl", "list", "short", kind]))
    if r.returncode != 0:
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


# ----------------------------- process runner -----------------------------

class ManagedProcess(QWidget):
    finished = Signal()

    def __init__(self, title: str, argv: list[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self.argv = argv
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setFont(QFont("monospace", 9))

        self.status = QLabel("Not started")
        self.stop_btn = QPushButton("Terminate")
        self.kill_btn = QPushButton("Kill")
        self.copy_btn = QPushButton("Copy command")

        top = QHBoxLayout()
        top.addWidget(QLabel(f"<b>{title}</b>"))
        top.addStretch(1)
        top.addWidget(self.status)
        top.addWidget(self.copy_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.kill_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.output)

        self.stop_btn.clicked.connect(self.proc.terminate)
        self.kill_btn.clicked.connect(self.proc.kill)
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(shell_join(self.argv)))
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)

    def start(self) -> None:
        if not have(self.argv[0]):
            self.output.appendPlainText(f"Missing tool: {self.argv[0]}")
            self.status.setText("missing")
            self.finished.emit()
            return
        self.output.appendPlainText(f"$ {shell_join(self.argv)}\n")
        self.status.setText("running")
        self.proc.start(self.argv[0], self.argv[1:])

    @Slot()
    def _read(self) -> None:
        data = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(data)
        self.output.moveCursor(QTextCursor.End)

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self.status.setText(f"finished rc={code} status={status.name}")
        self.finished.emit()

    @Slot(QProcess.ProcessError)
    def _error(self, err: QProcess.ProcessError) -> None:
        self.output.appendPlainText(f"\nQProcess error: {err.name}\n")


# ----------------------------- tabs ---------------------------------------

class InventoryTab(QWidget):
    def __init__(self):
        super().__init__()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Layer", "Item", "Details"])
        self.tree.setColumnWidth(0, 180)
        self.tree.setColumnWidth(1, 260)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.detail.setFont(QFont("monospace", 9))

        refresh = QPushButton("Refresh audio inventory")
        refresh.clicked.connect(self.refresh)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.detail)
        split.setSizes([350, 500])

        layout = QVBoxLayout(self)
        layout.addWidget(refresh)
        layout.addWidget(split)
        self.tree.itemSelectionChanged.connect(self.show_selected)
        self.refresh()

    def add_item(self, parent: QTreeWidgetItem, layer: str, item: str, details: str) -> QTreeWidgetItem:
        q = QTreeWidgetItem([layer, item, details.splitlines()[0] if details else ""])
        q.setData(0, Qt.UserRole, details)
        parent.addChild(q)
        return q

    def refresh(self) -> None:
        self.tree.clear()
        root = QTreeWidgetItem(["Audio", "Detected interfaces", "ALSA / PulseAudio / PipeWire / JACK"])
        root.setExpanded(True)
        self.tree.addTopLevelItem(root)

        alsa = QTreeWidgetItem(["ALSA", "Cards and PCM devices", "/proc/asound + aplay/arecord"])
        alsa.setExpanded(True)
        root.addChild(alsa)
        self.add_item(alsa, "ALSA", "/proc/asound/cards", read_text_file("/proc/asound/cards"))
        self.add_item(alsa, "ALSA", "/proc/asound/pcm", read_text_file("/proc/asound/pcm"))
        for d in get_playback_devices():
            self.add_item(alsa, "ALSA playback", d.hw, f"{d.name}\n{d.description}\nsubdevices available: {d.available_subdevices}")
        for d in get_capture_devices():
            self.add_item(alsa, "ALSA capture", d.hw, f"{d.name}\n{d.description}\nsubdevices available: {d.available_subdevices}")

        pulse = QTreeWidgetItem(["Pulse/PipeWire", "pactl objects", "Sinks, sources, cards, clients"])
        pulse.setExpanded(True)
        root.addChild(pulse)
        for kind in ["sinks", "sources", "sink-inputs", "source-outputs", "cards", "clients"]:
            rows = get_pactl_short(kind)
            parent = QTreeWidgetItem(["pactl", kind, f"{len(rows)} entries"])
            parent.setExpanded(False)
            pulse.addChild(parent)
            for row in rows:
                parts = row.split("\t")
                item = parts[1] if len(parts) > 1 else row[:80]
                self.add_item(parent, "pactl", item, row)

        pw = QTreeWidgetItem(["PipeWire", "wpctl / pw-cli", "Graph-level visibility when installed"])
        root.addChild(pw)
        for spec in [
            CmdSpec("wpctl status", ["wpctl", "status"]),
            CmdSpec("pw-cli info all", ["pw-cli", "info", "all"], timeout_ms=20_000),
        ]:
            r = run_cmd(spec)
            self.add_item(pw, spec.argv[0], shell_join(spec.argv), result_to_text(r))

        jack = QTreeWidgetItem(["JACK", "JACK graph", "Only populated if JACK server is running"])
        root.addChild(jack)
        for spec in [
            CmdSpec("jack_lsp", ["jack_lsp"]),
            CmdSpec("jack_lsp -Acl", ["jack_lsp", "-Acl"]),
            CmdSpec("jack_samplerate", ["jack_samplerate"]),
            CmdSpec("jack_bufsize", ["jack_bufsize"]),
        ]:
            r = run_cmd(spec)
            self.add_item(jack, "JACK", shell_join(spec.argv), result_to_text(r))

        self.tree.expandToDepth(1)
        self.detail.setPlainText("Select an audio object to view raw details.")

    def show_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        self.detail.setPlainText(items[0].data(0, Qt.UserRole) or "")


class SnapshotTab(QWidget):
    def __init__(self):
        super().__init__()
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setFont(QFont("monospace", 9))
        self.btn = QPushButton("Run full audio snapshot")
        self.save_btn = QPushButton("Save snapshot")
        self.copy_btn = QPushButton("Copy all")
        self.btn.clicked.connect(self.run_snapshot)
        self.save_btn.clicked.connect(self.save_snapshot)
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.out.toPlainText()))

        row = QHBoxLayout()
        row.addWidget(self.btn)
        row.addWidget(self.save_btn)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.out)

    def run_snapshot(self) -> None:
        self.btn.setEnabled(False)
        self.out.setPlainText("Running audio-only snapshot...\n")
        # Keep it simple and deterministic: run synchronously but allow GUI repaint.
        QApplication.processEvents()
        chunks = []
        chunks.append(f"{APP_NAME} {VERSION} audio snapshot\nGenerated: {now_stamp()}\n")
        chunks.append("CLI availability\n----------------")
        for t in sorted(required_and_optional_tools()):
            chunks.append(f"{t:18} {which(t) or '<missing>'}")
        chunks.append("")
        for spec in audio_snapshot_specs():
            QApplication.processEvents()
            r = run_cmd(spec)
            chunks.append(result_to_text(r))
            chunks.append("\n")
        for path in [
            "/proc/asound/cards", "/proc/asound/devices", "/proc/asound/modules",
            "/proc/asound/pcm", "/proc/asound/timers", "/proc/asound/version",
        ]:
            chunks.append(f"\n{'=' * 100}\n{path}\n{'=' * 100}\n{read_text_file(path)}")
        self.out.setPlainText("\n".join(chunks))
        self.btn.setEnabled(True)

    def save_snapshot(self) -> None:
        default = f"pi-audio-snapshot-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save audio snapshot", default, "Text files (*.txt);;All files (*)")
        if path:
            Path(path).write_text(self.out.toPlainText(), errors="replace")


class TestTab(QWidget):
    def __init__(self):
        super().__init__()
        self.jobs = QTabWidget()
        self.device = QComboBox()
        self.rate = QComboBox()
        self.rate.addItems(["8000", "16000", "22050", "32000", "44100", "48000", "88200", "96000", "176400", "192000"])
        self.rate.setCurrentText("48000")
        self.channels = QSpinBox()
        self.channels.setRange(1, 8)
        self.channels.setValue(2)
        self.duration = QSpinBox()
        self.duration.setRange(1, 3600)
        self.duration.setValue(10)
        self.freq = QSpinBox()
        self.freq.setRange(20, 22000)
        self.freq.setValue(1000)
        self.format = QComboBox()
        self.format.addItems(["S16_LE", "S24_LE", "S32_LE", "FLOAT_LE"])
        self.noise = QComboBox()
        self.noise.addItems(["sine", "pink", "white"])
        self.backend = QComboBox()
        self.backend.addItems(["speaker-test", "sox/play", "ffmpeg"])

        self.refresh_btn = QPushButton("Refresh devices")
        self.test_btn = QPushButton("Start playback test")
        self.capture_btn = QPushButton("Start capture test")
        self.loop_btn = QPushButton("Start arecord | aplay loopback")
        self.stop_all_btn = QPushButton("Terminate all jobs")

        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.test_btn.clicked.connect(self.start_playback)
        self.capture_btn.clicked.connect(self.start_capture)
        self.loop_btn.clicked.connect(self.start_loopback)
        self.stop_all_btn.clicked.connect(self.stop_all)

        form = QFormLayout()
        form.addRow("ALSA device", self.device)
        form.addRow("Sample rate", self.rate)
        form.addRow("Channels", self.channels)
        form.addRow("Sample format", self.format)
        form.addRow("Duration seconds", self.duration)
        form.addRow("Tone frequency", self.freq)
        form.addRow("Signal", self.noise)
        form.addRow("Playback backend", self.backend)

        box = QGroupBox("Parameterized audio test generator")
        box.setLayout(form)

        buttons = QHBoxLayout()
        for b in [self.refresh_btn, self.test_btn, self.capture_btn, self.loop_btn, self.stop_all_btn]:
            buttons.addWidget(b)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addLayout(buttons)
        layout.addWidget(self.jobs)
        self.refresh_devices()

    def refresh_devices(self) -> None:
        self.device.clear()
        self.device.addItem("default")
        for d in get_playback_devices():
            self.device.addItem(f"{d.hw}  [{d.name}] {d.description}", d.hw)
        for name in ["hw:0,0", "plughw:0,0", "sysdefault", "pulse", "pipewire"]:
            if self.device.findText(name) < 0:
                self.device.addItem(name, name)

    def current_device(self) -> str:
        data = self.device.currentData()
        return str(data or self.device.currentText().split()[0])

    def add_job(self, title: str, argv: list[str]) -> None:
        w = ManagedProcess(title, argv)
        idx = self.jobs.addTab(w, title[:28])
        self.jobs.setCurrentIndex(idx)
        w.start()

    def start_playback(self) -> None:
        dev = self.current_device()
        rate = self.rate.currentText()
        ch = str(self.channels.value())
        dur = str(self.duration.value())
        freq = str(self.freq.value())
        fmt = self.format.currentText()
        backend = self.backend.currentText()
        sig = self.noise.currentText()

        if backend == "speaker-test":
            if sig == "sine":
                argv = ["speaker-test", "-D", dev, "-r", rate, "-c", ch, "-F", fmt, "-t", "sine", "-f", freq, "-l", "1"]
            else:
                argv = ["speaker-test", "-D", dev, "-r", rate, "-c", ch, "-F", fmt, "-t", sig, "-l", "1"]
            self.add_job(f"speaker-test {dev}", argv)
            return

        if backend == "sox/play":
            # play is from sox. ALSA target via AUDIODEV is supported on many builds.
            synth = ["synth", dur]
            if sig == "sine":
                synth += ["sine", freq]
            elif sig == "pink":
                synth += ["pinknoise"]
            else:
                synth += ["whitenoise"]
            argv = ["play", "-q", "-n", "-r", rate, "-c", ch, "-b", "16"] + synth + ["gain", "-12"]
            self.add_job(f"sox play {sig}", argv)
            return

        # ffmpeg generated signal to ALSA sink
        lavfi = f"sine=frequency={freq}:sample_rate={rate}:duration={dur}"
        if sig == "white":
            lavfi = f"anoisesrc=color=white:sample_rate={rate}:duration={dur}:amplitude=0.2"
        elif sig == "pink":
            lavfi = f"anoisesrc=color=pink:sample_rate={rate}:duration={dur}:amplitude=0.2"
        argv = ["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", lavfi, "-ac", ch, "-f", "alsa", dev]
        self.add_job(f"ffmpeg -> {dev}", argv)

    def start_capture(self) -> None:
        cap_devs = get_capture_devices()
        dev = cap_devs[0].hw if cap_devs else self.current_device()
        rate = self.rate.currentText()
        ch = str(self.channels.value())
        dur = str(self.duration.value())
        fmt = self.format.currentText()
        out = Path(tempfile.gettempdir()) / f"pi-audio-capture-{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.wav"
        argv = ["arecord", "-D", dev, "-r", rate, "-c", ch, "-f", fmt, "-d", dur, str(out)]
        self.add_job(f"capture {dev}", argv)

    def start_loopback(self) -> None:
        cap_devs = get_capture_devices()
        play_dev = self.current_device()
        cap_dev = cap_devs[0].hw if cap_devs else "default"
        rate = self.rate.currentText()
        ch = str(self.channels.value())
        fmt = self.format.currentText()
        dur = str(self.duration.value())
        # Shell is used deliberately here for a pipeline. Tools are still standard CLI tools.
        cmd = (
            f"arecord -D {shlex.quote(cap_dev)} -r {shlex.quote(rate)} -c {shlex.quote(ch)} "
            f"-f {shlex.quote(fmt)} -d {shlex.quote(dur)} | "
            f"aplay -D {shlex.quote(play_dev)} -r {shlex.quote(rate)} -c {shlex.quote(ch)} -f {shlex.quote(fmt)}"
        )
        self.add_job("arecord | aplay", ["bash", "-lc", cmd])

    def stop_all(self) -> None:
        for i in range(self.jobs.count()):
            w = self.jobs.widget(i)
            if isinstance(w, ManagedProcess):
                w.proc.terminate()


class MixerTab(QWidget):
    def __init__(self):
        super().__init__()
        self.card = QComboBox()
        self.controls = QTreeWidget()
        self.controls.setHeaderLabels(["Control", "Value / capabilities"])
        self.controls.setColumnWidth(0, 380)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.raw.setFont(QFont("monospace", 9))
        self.refresh_btn = QPushButton("Refresh mixer")
        self.open_alsamixer_btn = QPushButton("Open alsamixer in terminal")
        self.refresh_btn.clicked.connect(self.refresh)
        self.open_alsamixer_btn.clicked.connect(self.open_alsamixer)

        top = QHBoxLayout()
        top.addWidget(QLabel("Card"))
        top.addWidget(self.card)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.open_alsamixer_btn)
        top.addStretch(1)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.controls)
        split.addWidget(self.raw)
        split.setSizes([350, 500])
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split)
        self.refresh_cards()
        self.refresh()

    def refresh_cards(self) -> None:
        self.card.clear()
        cards = parse_proc_asound_cards(read_text_file("/proc/asound/cards"))
        if not cards:
            self.card.addItem("default", "default")
        for num, label in cards:
            self.card.addItem(f"{num}: {label}", num)

    def refresh(self) -> None:
        card = str(self.card.currentData() or "default")
        argv = ["amixer"] if card == "default" else ["amixer", "-c", card]
        r = run_cmd(CmdSpec("amixer", argv, timeout_ms=20_000))
        self.raw.setPlainText(result_to_text(r))
        self.controls.clear()
        current: Optional[QTreeWidgetItem] = None
        for line in r.stdout.splitlines():
            if line.startswith("Simple mixer control"):
                name = line.replace("Simple mixer control", "").strip()
                current = QTreeWidgetItem([name, ""])
                self.controls.addTopLevelItem(current)
                current.setExpanded(True)
            elif current:
                m = re.search(r"\[(.*?)\]", line)
                val = m.group(1) if m else line.strip()
                current.addChild(QTreeWidgetItem([line.strip().split()[0] if line.strip() else "", val]))

    def open_alsamixer(self) -> None:
        # Try common terminal emulators available in Pi OS images.
        card = str(self.card.currentData() or "default")
        cmd = "alsamixer" if card == "default" else f"alsamixer -c {shlex.quote(card)}"
        for term in ["lxterminal", "x-terminal-emulator", "gnome-terminal", "konsole"]:
            if have(term):
                subprocess.Popen([term, "-e", cmd])
                return
        QMessageBox.warning(self, "No terminal found", "Could not find lxterminal, x-terminal-emulator, gnome-terminal, or konsole.")


class GraphTab(QWidget):
    def __init__(self):
        super().__init__()
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setFont(QFont("monospace", 9))
        self.cmd = QComboBox()
        self.cmd.addItems([
            "wpctl status",
            "pw-cli ls Node",
            "pw-cli ls Device",
            "pw-cli ls Port",
            "pw-cli info all",
            "pactl list short sinks",
            "pactl list short sources",
            "pactl list short sink-inputs",
            "pactl list short source-outputs",
            "jack_lsp -Acl",
        ])
        self.run_btn = QPushButton("Run graph query")
        self.run_btn.clicked.connect(self.run)
        top = QHBoxLayout()
        top.addWidget(QLabel("Query"))
        top.addWidget(self.cmd, 1)
        top.addWidget(self.run_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.out)

    def run(self) -> None:
        argv = shlex.split(self.cmd.currentText())
        r = run_cmd(CmdSpec(self.cmd.currentText(), argv, timeout_ms=30_000))
        append_block(self.out, self.cmd.currentText(), result_to_text(r))


class FileProbeTab(QWidget):
    def __init__(self):
        super().__init__()
        self.path = QLineEdit()
        self.pick = QPushButton("Pick audio file")
        self.probe = QPushButton("ffprobe")
        self.sox_stat = QPushButton("sox stat")
        self.play_btn = QPushButton("Play via aplay/play")
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setFont(QFont("monospace", 9))
        self.pick.clicked.connect(self.pick_file)
        self.probe.clicked.connect(self.run_ffprobe)
        self.sox_stat.clicked.connect(self.run_sox_stat)
        self.play_btn.clicked.connect(self.play_file)
        top = QHBoxLayout()
        top.addWidget(self.path, 1)
        top.addWidget(self.pick)
        top.addWidget(self.probe)
        top.addWidget(self.sox_stat)
        top.addWidget(self.play_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.out)

    def pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pick audio file", str(Path.home()), "Audio files (*.wav *.flac *.mp3 *.ogg *.opus *.m4a *.aac);;All files (*)")
        if path:
            self.path.setText(path)

    def valid_path(self) -> Optional[str]:
        p = self.path.text().strip()
        if not p:
            QMessageBox.warning(self, "No file", "Choose an audio file first.")
            return None
        return p

    def run_ffprobe(self) -> None:
        p = self.valid_path()
        if not p:
            return
        r = run_cmd(CmdSpec("ffprobe", ["ffprobe", "-hide_banner", "-show_format", "-show_streams", p], timeout_ms=30_000))
        append_block(self.out, "ffprobe", result_to_text(r))

    def run_sox_stat(self) -> None:
        p = self.valid_path()
        if not p:
            return
        r = run_cmd(CmdSpec("sox stat", ["sox", p, "-n", "stat"], timeout_ms=30_000))
        append_block(self.out, "sox stat", result_to_text(r))

    def play_file(self) -> None:
        p = self.valid_path()
        if not p:
            return
        if p.lower().endswith(".wav") and have("aplay"):
            subprocess.Popen(["aplay", p])
            append_block(self.out, "aplay", f"Started: aplay {shlex.quote(p)}")
        elif have("play"):
            subprocess.Popen(["play", p])
            append_block(self.out, "play", f"Started: play {shlex.quote(p)}")
        else:
            QMessageBox.warning(self, "Missing player", "Install alsa-utils or sox.")


class WatchTab(QWidget):
    def __init__(self):
        super().__init__()
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.out.setFont(QFont("monospace", 9))
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setValue(3)
        self.running = QCheckBox("Poll")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.running.toggled.connect(self.toggle)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.out.clear)
        top = QHBoxLayout()
        top.addWidget(self.running)
        top.addWidget(QLabel("Interval seconds"))
        top.addWidget(self.interval)
        top.addWidget(self.clear_btn)
        top.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.out)

    def toggle(self, on: bool) -> None:
        if on:
            self.timer.start(self.interval.value() * 1000)
            self.poll()
        else:
            self.timer.stop()

    def poll(self) -> None:
        chunks = [f"[{now_stamp()}]"]
        for spec in [
            CmdSpec("pactl short sinks", ["pactl", "list", "short", "sinks"], timeout_ms=5000),
            CmdSpec("pactl short sources", ["pactl", "list", "short", "sources"], timeout_ms=5000),
            CmdSpec("wpctl status", ["wpctl", "status"], timeout_ms=5000),
            CmdSpec("fuser /dev/snd", ["fuser", "-v", "/dev/snd/*"], timeout_ms=5000),
        ]:
            r = run_cmd(spec)
            chunks.append(f"\n--- {spec.title} ---\n{(r.stdout or r.stderr).rstrip()}")
        self.out.appendPlainText("\n".join(chunks) + "\n")
        self.out.moveCursor(QTextCursor.End)


# ----------------------------- main window --------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(1320, 880)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(InventoryTab(), "Inventory")
        self.tabs.addTab(SnapshotTab(), "Snapshot")
        self.tabs.addTab(TestTab(), "Tests")
        self.tabs.addTab(MixerTab(), "Mixer")
        self.tabs.addTab(GraphTab(), "Graph")
        self.tabs.addTab(FileProbeTab(), "File probe")
        self.tabs.addTab(WatchTab(), "Watch")
        self._build_menu()
        self.statusBar().showMessage("Audio-only engineering diagnostics. CLI-backed. No general system telemetry.")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("Help")
        about_action = QAction("About / apt packages", self)
        about_action.triggered.connect(self.about)
        help_menu.addAction(about_action)

    def about(self) -> None:
        QMessageBox.information(
            self,
            "About Pi Audio Lab",
            f"{APP_NAME} {VERSION}\n\n"
            "Audio-only Raspberry Pi OS engineering console.\n\n"
            "Core apt packages:\n"
            "  python3-pyside6.qtwidgets python3-pyside6.qtcore alsa-utils\n"
            "  pulseaudio-utils pipewire-bin wireplumber sox ffmpeg lsof procps\n\n"
            "Optional:\n"
            "  jackd2 qjackctl pavucontrol\n",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Engineering Audio Lab")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
