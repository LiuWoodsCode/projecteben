import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import URLError
import requests
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QVBoxLayout,
    QPushButton,
    QFrame
)

DEVICE_PORT = 8000

# Ethernet → Wi‑Fi
DEVICE_HOSTS = [
    "10.42.1.1",
    "10.42.0.1"
]

TIMEOUT = 2


# -------------------------------------------------
# Reachability check
# -------------------------------------------------
def device_alive(host):
    try:
        with urlopen(f"http://{host}:{DEVICE_PORT}/device/hostname",
                     timeout=TIMEOUT):
            return True
    except URLError:
            return False

def get_hostname(host):
    try:
        hn = requests.get(f"http://{host}:{DEVICE_PORT}/device/hostname", timeout=TIMEOUT)
        return hn.text
    except:
        return "Error"

# -------------------------------------------------
# Background Detector
# -------------------------------------------------
class DetectWorker(QThread):
    deviceFound = Signal(str)
    failed = Signal()

    def run(self):
        attempts = 0

        while attempts < 5:
            for host in DEVICE_HOSTS:
                if device_alive(host):
                    self.deviceFound.emit(host)
                    return
            attempts += 1
            time.sleep(1)

        self.failed.emit()


# -------------------------------------------------
# Viewer Watchdog
# -------------------------------------------------
class ViewerSupervisor(QObject):
    viewerExited = Signal()

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.process = None

    def start(self):
        cmd = [
            sys.executable,
            "main.py",
            "--host", self.host,
            "--force-no-global-menu",
            "--fullscreen"
        ]

        self.process = subprocess.Popen(
            cmd,
            start_new_session=True
        )

        QThread(target=self._wait).start()

    def _wait(self):
        self.process.wait()
        self.viewerExited.emit()


# -------------------------------------------------
# Main Thin Client UI
# -------------------------------------------------
class ThinClientWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.CustomizeWindowHint
        )

        self.setCursor(Qt.BlankCursor)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        # Floating Surface
        self.surface = QFrame()
        self.surface.setObjectName("surface")
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setSpacing(10)
        surface_layout.setAlignment(Qt.AlignCenter)

        # Title (Segoe hierarchy)
        self.title = QLabel("Ranboo")
        self.title.setObjectName("title")

        self.subtitle = QLabel("Connecting to your device...")
        self.subtitle.setObjectName("subtitle")

        self.body = QLabel("")
        self.body.setObjectName("body")

        self.retry = QPushButton("Try again")
        self.retry.setObjectName("retryButton")
        self.retry.hide()
        self.retry.clicked.connect(self.begin_detection)

        surface_layout.addWidget(self.title)
        surface_layout.addWidget(self.subtitle)
        surface_layout.addWidget(self.body)
        surface_layout.addWidget(self.retry)

        layout.addWidget(self.surface)

        self.supervisor = None
        self.begin_detection()

    # -----------------------------------------
    def begin_detection(self):
        self.retry.hide()
        self.subtitle.setText("Connecting to your device...")
        self.body.setText("")

        self.detector = DetectWorker()
        self.detector.deviceFound.connect(self.launch_viewer)
        self.detector.failed.connect(self.show_failure)
        self.detector.start()

    # -----------------------------------------
    def show_failure(self):
        self.subtitle.setText("Can't find your device")

        self.body.setText(
            "Make sure it is:\n"
            "• Plugged into power\n"
            "• Connected by Ethernet\n"
            "• On the same network"
        )

        self.retry.show()

    # -----------------------------------------
    def launch_viewer(self, host):
        hst = get_hostname(host)
        self.subtitle.setText(f"Connecting to {hst}...")

        self.supervisor = ViewerSupervisor(host)
        self.supervisor.viewerExited.connect(self.restart_flow)
        self.supervisor.start()

        self.hide()

    # -----------------------------------------
    def restart_flow(self):
        self.show()
        self.begin_detection()


# -------------------------------------------------
# Entry
# -------------------------------------------------
def main():

    QApplication.setAttribute(
        Qt.ApplicationAttribute.AA_DontUseNativeMenuBar,
        True
    )

    app = QApplication(sys.argv)

    app.setStyleSheet("""
        QFrame#surface {
            background: rgba(32,32,32,0.84);
            border-radius: 16px;
            padding: 1px;
        }

        QLabel#title {
            font: 600 28px "Segoe UI";
            color: white;
        }

        QLabel#subtitle {
            font: 400 18px "Segoe UI";
            color: #E6E6E6;
        }

        QLabel#body {
            font: 400 14px "Segoe UI";
            color: #C8C8C8;
        }

        QPushButton#retryButton {
            margin-top: 16px;
            padding: 8px 16px;
            font: 600 14px "Segoe UI";
            background-color: #0067C0;
            border-radius: 4px;
            color: white;
        }
    """)

    win = ThinClientWindow()
    win.showFullScreen()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()