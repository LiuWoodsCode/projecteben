import sys
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QPlainTextEdit,
    QWidget,
    QVBoxLayout,
    QLabel,
)

import dict_tools
from edid_parser import parse_edid


DRM_DIR = Path("/sys/class/drm")


def read_connected_edids() -> list[tuple[str, object]]:
    monitors = []

    for connector_dir in sorted(DRM_DIR.glob("card*-*")):
        status_file = connector_dir / "status"
        edid_file = connector_dir / "edid"

        if not status_file.is_file() or not edid_file.is_file():
            continue

        try:
            status = status_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        if status != "connected":
            continue

        try:
            edid_bytes = edid_file.read_bytes()
        except OSError:
            continue

        if not edid_bytes:
            continue

        edid = parse_edid(edid_bytes.hex())
        monitors.append((connector_dir.name, edid))

    return monitors


class MonitorTab(QWidget):
    def __init__(self, connector_name: str, edid_obj: object):
        super().__init__()

        layout = QVBoxLayout(self)

        # Header label (nice to have context)
        title = QLabel(f"{connector_name} — {edid_obj.manufacturer_id}")
        layout.addWidget(title)

        # Convert EDID to dict → JSON string
        edid_dict = dict_tools.to_dict(edid_obj)
        json_text = json.dumps(edid_dict, indent=2)

        # Text view
        text_view = QPlainTextEdit()
        text_view.setReadOnly(True)
        text_view.setPlainText(json_text)

        layout.addWidget(text_view)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("EDID Viewer")

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        monitors = read_connected_edids()

        if not monitors:
            empty = QWidget()
            layout = QVBoxLayout(empty)
            layout.addWidget(QLabel("No connected displays with readable EDID were found."))
            tabs.addTab(empty, "No Displays")
            return

        for connector_name, edid in monitors:
            tab = MonitorTab(connector_name, edid)
            tab_title = f"{connector_name}"
            tabs.addTab(tab, tab_title)


def main():
    app = QApplication(sys.argv)

    window = MainWindow()
    window.resize(800, 600)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()