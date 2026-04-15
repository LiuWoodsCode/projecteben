import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from PySide6.QtCore import QObject, QRunnable, Qt, QSize, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenuBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


DEVICE_PORT = 8000
DEVICE_HOSTS = ["10.42.0.1", "10.42.1.1"]


@dataclass
class DeviceSnapshot:
    host: str
    hostname: str = "Unknown"
    model: str = "Unknown"
    serial: str = "Unknown"
    revision: str = "Unknown"
    kernel_version: str = "Unknown"
    kernel_cmdline: str = "Unknown"
    uptime: str = "Unknown"
    cpu_temp: str = "Unknown"
    gpu_temp: str = "Unknown"
    memory: str = "Unknown"
    disks: str = "Unknown"
    error: str | None = None
    unavailable: bool = False

    @property
    def title(self) -> str:
        if self.hostname != "Unknown":
            return self.hostname
        if self.model != "Unknown":
            return self.model
        return "Unavailable" if self.unavailable else self.host

    @property
    def subtitle(self) -> str:
        if self.error:
            return self.error
        if self.model != "Unknown":
            return self.model
        if self.hostname != "Unknown":
            return self.hostname
        return "Unavailable" if self.unavailable else "Online"


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str, str)


class Worker(QRunnable):
    def __init__(self, description: str, job):
        super().__init__()
        self.description = description
        self.job = job
        self.signals = WorkerSignals()
        # Keep QRunnable alive until Python-side callbacks run.
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.job()
            self.signals.finished.emit(result)
        except Exception as exc:
            print(f"[worker] {self.description}: {exc!r}", file=sys.stderr)
            self.signals.failed.emit(self.description, repr(exc))


class RemoteDeviceClient:
    def __init__(self, host: str, port: int = DEVICE_PORT, timeout: int = 3):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    def _request(self, path: str) -> tuple[str, str]:
        with urlopen(self._url(path), timeout=self.timeout) as response:
            raw = response.read()
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace").strip()
            return text, content_type

    def _log_network_exception(self, path: str, exc: Exception):
        print(f"[network] {self.host}{path}: {exc!r}", file=sys.stderr)

    def _run_parallel(self, jobs: dict[str, Any]) -> dict[str, Any]:
        if not jobs:
            return {}

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            future_to_key = {
                executor.submit(job): key
                for key, job in jobs.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                results[key] = future.result()

        return results

    def get_text(self, path: str, default: str = "Unknown") -> str:
        try:
            text, _ = self._request(path)
            return text or default
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            self._log_network_exception(path, exc)
            return default

    def get_json(self, path: str, default: Any = None) -> Any:
        try:
            text, content_type = self._request(path)
            if not text:
                return default
            if content_type == "application/json" or text.startswith(("{", "[")):
                return json.loads(text)
            return default
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            self._log_network_exception(path, exc)
            return default

    def fetch_summary(self) -> DeviceSnapshot:
        try:
            results = self._run_parallel({
                "hostname": lambda: self.get_text("/device/hostname", default="Unknown"),
                "model": lambda: self.get_text("/device/model", default="Unknown"),
            })
            hostname = results.get("hostname", "Unknown")
            model = results.get("model", "Unknown")
            unavailable = hostname == "Unknown" and model == "Unknown"

            return DeviceSnapshot(
                host=self.host,
                hostname=hostname,
                model=model,
                unavailable=unavailable,
            )
        except Exception as exc:
            return DeviceSnapshot(host=self.host, error=str(exc), unavailable=True)

    def fetch_details(self) -> DeviceSnapshot:
        try:
            results = self._run_parallel({
                "hostname": lambda: self.get_text("/device/hostname", default="Unknown"),
                "model": lambda: self.get_text("/device/model", default="Unknown"),
                "serial": lambda: self.get_text("/device/serial", default="Unknown"),
                "revision": lambda: self.get_text("/device/revision", default="Unknown"),
                "kernel_version": lambda: self.get_text("/kernel/version", default="Unknown"),
                "kernel_cmdline": lambda: self.get_text("/kernel/cmdline", default="Unknown"),
                "uptime": lambda: self.get_text("/session/uptime", default="Unknown"),
                "cpu_temp": lambda: self.get_text("/thermal/cpu", default="Unknown"),
                "gpu_temp": lambda: self.get_text("/thermal/gpu", default="Unknown"),
                "memory": lambda: self.get_json("/device/resource/mem", default={}) or {},
                "disks": lambda: self.get_text("/device/resource/disk", default=[]) or [],
            })

            hostname = results.get("hostname", "Unknown")
            model = results.get("model", "Unknown")
            unavailable = hostname == "Unknown" and model == "Unknown"

            return DeviceSnapshot(
                host=self.host,
                hostname=hostname,
                model=model,
                serial=results.get("serial", "Unknown"),
                revision=results.get("revision", "Unknown"),
                kernel_version=results.get("kernel_version", "Unknown"),
                kernel_cmdline=results.get("kernel_cmdline", "Unknown"),
                uptime=self._format_uptime(results.get("uptime", "Unknown")),
                cpu_temp=self._format_temperature(results.get("cpu_temp", "Unknown")),
                gpu_temp=self._format_temperature(results.get("gpu_temp", "Unknown")),
                memory=self._format_memory(results.get("memory", {})),
                disks=results.get("disks", "Unknown"),
                unavailable=unavailable,
            )
        except Exception as exc:
            return DeviceSnapshot(host=self.host, error=str(exc), unavailable=True)

    @staticmethod
    def _format_temperature(value: str) -> str:
        try:
            numeric = float(value)
            return f"{numeric:.1f} C"
        except (TypeError, ValueError):
            return value or "Unknown"

    @staticmethod
    def _format_memory(value: Any) -> str:
        if not isinstance(value, dict):
            return "Unknown"

        total = value.get("total_mib")
        used = value.get("used_mib")
        free = value.get("free_mib")
        if total is None or used is None or free is None:
            return "Unknown"

        return f"{used} MiB used / {total} MiB total ({free} MiB free)"

    @staticmethod
    def _format_disk_count(value: Any) -> str:
        if isinstance(value, list):
            return f"{len(value)} mounted volume(s)"
        return "Unknown"

    @staticmethod
    def _format_uptime(value: str) -> str:
        if not value or value == "Unknown":
            return "Unknown"

        try:
            uptime_seconds = float(value.split()[0])
        except (ValueError, IndexError):
            return value

        minutes, seconds = divmod(int(uptime_seconds), 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        if days:
            return f"{days}d {hours}h {minutes}m"
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"


class DeviceListItem(QWidget):
    def __init__(self, name: str, subtitle: str, unavailable: bool = False):
        super().__init__()

        self.name_label = QLabel(name)
        self.subtitle_label = QLabel(subtitle)

        self.name_label.setObjectName("deviceName")
        self.subtitle_label.setObjectName("deviceSubtitle")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(self.name_label)
        layout.addWidget(self.subtitle_label)

        if unavailable:
            self.setProperty("unavailable", True)
            self.name_label.setProperty("unavailable", True)
            self.subtitle_label.setProperty("unavailable", True)

    def update_subtitle(self, subtitle: str):
        self.subtitle_label.setText(subtitle)


class DevicePanel(QWidget):
    refreshRequested = Signal()

    def __init__(self):
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header.setObjectName("sidebarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 10, 10)
        header_layout.setSpacing(8)

        title = QLabel("Devices")
        title.setObjectName("sidebarTitle")

        refresh_button = QPushButton("⟳")
        refresh_button.setObjectName("refreshButton")
        refresh_button.setCursor(Qt.PointingHandCursor)
        refresh_button.setFixedSize(28, 28)
        refresh_button.clicked.connect(self.refreshRequested.emit)

        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(refresh_button)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("deviceList")
        self.list_widget.setSpacing(0)
        self.list_widget.setFrameShape(QFrame.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        root.addWidget(header)
        root.addWidget(self.list_widget, 1)

        for host in DEVICE_HOSTS:
            self.add_device(host, "Loading system info...")

    def add_device(self, name: str, subtitle: str, unavailable: bool = False):
        item = QListWidgetItem()
        widget = DeviceListItem(name, subtitle, unavailable=unavailable)

        item.setSizeHint(QSize(260, 64))
        item.setData(Qt.ItemDataRole.UserRole, name)

        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def update_device(self, row: int, snapshot: DeviceSnapshot):
        item = self.list_widget.item(row)
        if item is None:
            return

        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, DeviceListItem):
            widget.name_label.setText(snapshot.title)
            widget.update_subtitle(snapshot.subtitle)
            if snapshot.unavailable:
                widget.setProperty("unavailable", True)
                widget.name_label.setProperty("unavailable", True)
                widget.subtitle_label.setProperty("unavailable", True)
            else:
                widget.setProperty("unavailable", False)
                widget.name_label.setProperty("unavailable", False)
                widget.subtitle_label.setProperty("unavailable", False)


class DetailPanel(QWidget):
    def __init__(self):
        super().__init__()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QWidget()
        self.header.setObjectName("detailHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(8)

        self.title_label = QLabel("Sayori’s Raspberry Pi 5")
        self.title_label.setObjectName("detailTitle")

        header_stack = QVBoxLayout()
        header_stack.setContentsMargins(0, 0, 0, 0)
        header_stack.setSpacing(2)
        header_stack.addWidget(self.title_label)

        header_layout.addLayout(header_stack)
        header_layout.addStretch()

        self.content = QScrollArea()
        self.content.setObjectName("detailContent")
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.content.setWidgetResizable(True)

        self.content_body = QWidget()
        self.content_body.setObjectName("detailBody")
        self.content.setWidget(self.content_body)

        body_layout = QVBoxLayout(self.content_body)
        body_layout.setContentsMargins(16, 16, 16, 16)
        body_layout.setSpacing(12)

        self.rows: dict[str, QLabel] = {}
        self.row_widgets: dict[str, QWidget] = {}

        self.unreachable_label = QLabel("This device is unreachable.")
        self.unreachable_label.setObjectName("detailUnreachable")
        self.unreachable_label.setWordWrap(True)
        self.unreachable_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unreachable_label.hide()
        body_layout.addWidget(self.unreachable_label)

        for label in [
            "Host",
            "Hostname",
            "Model",
            "Serial",
            "Revision",
            "Kernel Version",
            "Uptime",
            "CPU Temperature",
            "GPU Temperature",
            "Memory",
            "Mounted Volumes",
        ]:
            body_layout.addWidget(self._build_info_row(label))

        body_layout.addWidget(self._build_info_row("Kernel Cmdline", multiline=True))
        body_layout.addStretch()

        root.addWidget(self.header)
        root.addWidget(self.content, 1)

    def _build_info_row(self, title: str, multiline: bool = False) -> QWidget:
        row = QWidget()
        row.setObjectName("infoRow")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("infoRowTitle")

        value_label = QLabel("-")
        value_label.setObjectName("infoRowValue")
        value_label.setWordWrap(multiline)

        layout.addWidget(title_label)
        layout.addWidget(value_label)

        self.rows[title] = value_label
        self.row_widgets[title] = row
        return row

    def _set_rows_visible(self, visible: bool):
        for row in self.row_widgets.values():
            row.setVisible(visible)

    def show_unreachable(self, snapshot: DeviceSnapshot):
        self.title_label.setText(snapshot.title)
        self._set_rows_visible(False)
        self.unreachable_label.show()

    def set_snapshot(self, snapshot: DeviceSnapshot):
        if snapshot.unavailable:
            self.show_unreachable(snapshot)
            return

        self.unreachable_label.hide()
        self._set_rows_visible(True)
        self.title_label.setText(snapshot.title)

        self.rows["Host"].setText(snapshot.host)
        self.rows["Hostname"].setText(snapshot.hostname)
        self.rows["Model"].setText(snapshot.model)
        self.rows["Serial"].setText(snapshot.serial)
        self.rows["Revision"].setText(snapshot.revision)
        self.rows["Kernel Version"].setText(snapshot.kernel_version)
        self.rows["Uptime"].setText(snapshot.uptime)
        self.rows["CPU Temperature"].setText(snapshot.cpu_temp)
        self.rows["GPU Temperature"].setText(snapshot.gpu_temp)
        self.rows["Memory"].setText(snapshot.memory)
        self.rows["Mounted Volumes"].setText(snapshot.disks)
        self.rows["Kernel Cmdline"].setText(snapshot.kernel_cmdline)

    def show_loading(self, title: str, subtitle: str):
        self.unreachable_label.hide()
        self._set_rows_visible(True)
        self.title_label.setText(title)
        for label in self.rows.values():
            label.setText("Loading...")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Pi Device Browser")
        self.resize(1140, 720)
        self.setMinimumSize(900, 560)

        self._create_menu()

        central = QWidget()
        self.setCentralWidget(central)

        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        outer_layout.setSpacing(0)

        outer_frame = QFrame()
        outer_frame.setObjectName("outerFrame")
        frame_layout = QVBoxLayout(outer_frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setHandleWidth(1)

        self.device_panel = DevicePanel()
        self.detail_panel = DetailPanel()
        self.device_clients = [RemoteDeviceClient(host) for host in DEVICE_HOSTS]
        self.device_snapshots: list[DeviceSnapshot] = []
        self.thread_pool = QThreadPool.globalInstance()
        self.summary_generation = 0
        self.detail_generation = 0
        self.active_workers: set[Worker] = set()

        splitter.addWidget(self.device_panel)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 830])

        frame_layout.addWidget(splitter)
        outer_layout.addWidget(outer_frame)

        self.device_panel.list_widget.currentRowChanged.connect(self.on_device_changed)
        self.device_panel.refreshRequested.connect(self.refresh_selected_device)
        self.refresh_all_devices()
        self.device_panel.list_widget.setCurrentRow(0)

    def _create_menu(self):
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("File")
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def on_device_changed(self, row: int):
        if row < 0 or row >= len(self.device_clients):
            return

        cached = self.device_snapshots[row] if row < len(self.device_snapshots) else None
        if cached is not None:
            self.detail_panel.show_loading(cached.title, f"Loading live system information from {cached.host}...")
        else:
            host = self.device_clients[row].host
            self.detail_panel.show_loading(host, f"Loading live system information from {host}...")

        self.refresh_device_details(row)

    def refresh_all_devices(self):
        self.summary_generation += 1
        generation = self.summary_generation

        for row, client in enumerate(self.device_clients):
            worker = Worker(
                f"summary:{client.host}",
                lambda client=client: client.fetch_summary(),
            )
            self._start_worker(
                worker,
                lambda snapshot, row=row, generation=generation: self._handle_summary_result(row, generation, snapshot),
                lambda _description, error, row=row, generation=generation: self._handle_summary_failure(row, generation, error),
            )

    def refresh_selected_device(self):
        self.refresh_all_devices()

    def refresh_device_details(self, row: int):
        if row < 0 or row >= len(self.device_clients):
            return

        self.detail_generation += 1
        generation = self.detail_generation
        client = self.device_clients[row]

        worker = Worker(
            f"details:{client.host}",
            lambda client=client: client.fetch_details(),
        )
        self._start_worker(
            worker,
            lambda snapshot, row=row, generation=generation: self._handle_detail_result(row, generation, snapshot),
            lambda _description, error, row=row, generation=generation: self._handle_detail_failure(row, generation, error),
        )

    def _start_worker(self, worker: Worker, finished_cb, failed_cb):
        self.active_workers.add(worker)

        def _on_finished(payload):
            try:
                finished_cb(payload)
            finally:
                self._release_worker(worker)

        def _on_failed(description: str, error: str):
            try:
                failed_cb(description, error)
            finally:
                self._release_worker(worker)

        worker.signals.finished.connect(_on_finished)
        worker.signals.failed.connect(_on_failed)
        self.thread_pool.start(worker)

    def _release_worker(self, worker: Worker):
        self.active_workers.discard(worker)

    def _handle_summary_result(self, row: int, generation: int, snapshot: DeviceSnapshot):
        if generation != self.summary_generation or row >= len(self.device_clients):
            return

        if row >= len(self.device_snapshots):
            self.device_snapshots.extend([DeviceSnapshot(host=client.host) for client in self.device_clients[len(self.device_snapshots):row + 1]])

        self.device_snapshots[row] = snapshot
        self.device_panel.update_device(row, snapshot)

    def _handle_summary_failure(self, row: int, generation: int, error: str):
        if generation != self.summary_generation or row >= len(self.device_clients):
            return

        snapshot = DeviceSnapshot(host=self.device_clients[row].host, error=error, unavailable=True)
        if row >= len(self.device_snapshots):
            self.device_snapshots.extend([DeviceSnapshot(host=client.host) for client in self.device_clients[len(self.device_snapshots):row + 1]])
        self.device_snapshots[row] = snapshot
        self.device_panel.update_device(row, snapshot)

    def _handle_detail_result(self, row: int, generation: int, snapshot: DeviceSnapshot):
        if generation != self.detail_generation or row != self.device_panel.list_widget.currentRow():
            return

        self.detail_panel.set_snapshot(snapshot)

        if row < len(self.device_snapshots):
            self.device_snapshots[row] = snapshot
            self.device_panel.update_device(row, snapshot)

    def _handle_detail_failure(self, row: int, generation: int, error: str):
        if generation != self.detail_generation or row != self.device_panel.list_widget.currentRow():
            return

        snapshot = DeviceSnapshot(host=self.device_clients[row].host, error=error, unavailable=True)
        self.detail_panel.set_snapshot(snapshot)


def apply_dark_palette(app: QApplication):
    pass


def apply_styles(app: QApplication):
    app.setStyleSheet("""
        QMenuBar {
            padding: 2px 6px;
        }

        QMenuBar::item {
            padding: 4px 10px;
        }

        QMenu {
        }

        QFrame#outerFrame {
        }

        QSplitter::handle {
        }

        QWidget#sidebarHeader {
        }

        QLabel#sidebarTitle {
            font-size: 18px;
            font-weight: 700;
        }

        QPushButton#refreshButton {
            font-size: 20px;
            font-weight: 600;
        }

        QListWidget#deviceList {
        }

        QListWidget#deviceList::item {
        }

        QLabel#deviceName {
            font-size: 16px;
            font-weight: 700;
        }

        QLabel#deviceSubtitle {
            font-size: 12px;
        }

        QWidget#detailHeader {
        }

        QLabel#detailTitle {
            font-size: 18px;
            font-weight: 700;
            padding-bottom: 2px;
        }

        QFrame#detailContent {
        }
    """)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--force-no-global-menu", action="store_true")
    args, qt_args = parser.parse_known_args()

    if args.force_no_global_menu:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Eben Desktop")

    apply_dark_palette(app)
    apply_styles(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()