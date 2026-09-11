import sys
import subprocess
import threading
import queue
import re
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLineEdit, QLabel, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QSplitter, QMenuBar, QMenu
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction

CEC_PATH = "cec-client"

class CECWorker(threading.Thread):
    def __init__(self, adapter="RPI", monitor=True, debug=True):
        super().__init__(daemon=True)
        self.adapter = adapter
        self.monitor = monitor
        self.debug = debug
        self.proc = None
        self.running = False
        self.out_queue = queue.Queue()

    def run(self):
        cmd = [CEC_PATH, "-t", "p"]
        if self.adapter:
            cmd += ["-d", "1" if self.debug else "0"]
        if self.monitor:
            cmd += ["-m"]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        self.running = True
        for line in self.proc.stdout:
            self.out_queue.put(line)
        self.running = False

    def send(self, command: str):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(command + "\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None
        self.running = False


class CECAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HDMI-CEC Engineering Console")
        self.resize(1400, 900)

        self.worker = None

        self._build_ui()
        self._build_menus()

        self.timer = QTimer()
        self.timer.timeout.connect(self._poll_output)
        self.timer.start(50)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        ctrl_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.debug_chk = QCheckBox("Debug")
        self.monitor_chk = QCheckBox("Monitor Mode")
        self.monitor_chk.setChecked(True)

        self.adapter_combo = QComboBox()
        self.adapter_combo.addItems(["RPI", "Linux", "USB"])

        ctrl_layout.addWidget(QLabel("Adapter:"))
        ctrl_layout.addWidget(self.adapter_combo)
        ctrl_layout.addWidget(self.debug_chk)
        ctrl_layout.addWidget(self.monitor_chk)
        ctrl_layout.addWidget(self.start_btn)
        ctrl_layout.addWidget(self.stop_btn)

        layout.addLayout(ctrl_layout)

        splitter = QSplitter(Qt.Vertical)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        splitter.addWidget(self.log)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Time", "Source", "Destination", "Opcode", "Raw"
        ])
        splitter.addWidget(self.table)

        layout.addWidget(splitter)

        cmd_layout = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.send_btn = QPushButton("Send")

        cmd_layout.addWidget(QLabel("CEC Command:"))
        cmd_layout.addWidget(self.cmd_input)
        cmd_layout.addWidget(self.send_btn)

        layout.addLayout(cmd_layout)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.send_btn.clicked.connect(self.send_command)

    def _build_menus(self):
        menubar = self.menuBar()

        def add_action(menu, name, cmd):
            act = QAction(name, self)
            act.triggered.connect(lambda: self.send_predefined(cmd))
            menu.addAction(act)

        # One Touch Play
        otp = menubar.addMenu("One Touch Play")
        add_action(otp, "Image View On", "tx 10:04")
        add_action(otp, "Active Source", "tx 10:82:10:00")

        # Routing Control
        routing = menubar.addMenu("Routing Control")
        add_action(routing, "Request Active Source", "tx 1F:85")
        add_action(routing, "Set Stream Path", "tx 10:86:10:00")

        # Remote Control
        remote = menubar.addMenu("Remote Control")
        add_action(remote, "Volume Up", "tx 10:44:41")
        add_action(remote, "Volume Down", "tx 10:44:42")
        add_action(remote, "Mute", "tx 10:44:43")

        # Deck Control
        deck = menubar.addMenu("Deck Control")
        add_action(deck, "Play", "tx 10:44:44")
        add_action(deck, "Pause", "tx 10:44:46")
        add_action(deck, "Stop", "tx 10:44:45")

        # Power / Standby
        power = menubar.addMenu("Power")
        add_action(power, "Standby", "tx 10:36")
        add_action(power, "Give Power Status", "tx 10:8F")

        # Audio Control
        audio = menubar.addMenu("System Audio")
        add_action(audio, "System Audio On", "tx 10:72:01")
        add_action(audio, "System Audio Off", "tx 10:72:00")

        # Tuner Control
        tuner = menubar.addMenu("Tuner")
        add_action(tuner, "Give Tuner Device Status", "tx 10:08")

        # Recording
        record = menubar.addMenu("Recording")
        add_action(record, "Record On", "tx 10:09")
        add_action(record, "Record Off", "tx 10:0B")

        # Timer
        timer = menubar.addMenu("Timer")
        add_action(timer, "Set Timer", "tx 10:34")

        # OSD
        osd = menubar.addMenu("OSD")
        add_action(osd, "Display 'HELLO'", "tx 10:64:48:45:4C:4C:4F")

        # Lipsync
        lipsync = menubar.addMenu("Lipsync")
        add_action(lipsync, "Report Delay", "tx 10:A7:00:64")

    def send_predefined(self, cmd):
        if self.worker:
            self.worker.send(cmd)
            self.log.append(f"[TX-MENU] {cmd}")

    def start(self):
        if self.worker and self.worker.running:
            return

        self.worker = CECWorker(
            adapter=self.adapter_combo.currentText(),
            monitor=self.monitor_chk.isChecked(),
            debug=self.debug_chk.isChecked()
        )
        self.worker.start()
        self.log.append("[INFO] CEC session started")

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.log.append("[INFO] CEC session stopped")

    def send_command(self):
        cmd = self.cmd_input.text().strip()
        if cmd and self.worker:
            self.worker.send(cmd)
            self.log.append(f"[TX] {cmd}")
            self.cmd_input.clear()

    def _poll_output(self):
        if not self.worker:
            return

        while not self.worker.out_queue.empty():
            line = self.worker.out_queue.get()
            self._handle_line(line)

    def _handle_line(self, line: str):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.append(line.rstrip())

        parsed = self._parse_cec(line)
        if parsed:
            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row, 0, QTableWidgetItem(timestamp))
            self.table.setItem(row, 1, QTableWidgetItem(parsed['src']))
            self.table.setItem(row, 2, QTableWidgetItem(parsed['dst']))
            self.table.setItem(row, 3, QTableWidgetItem(parsed['opcode']))
            self.table.setItem(row, 4, QTableWidgetItem(parsed['raw']))

    def _parse_cec(self, line: str):
        m = re.search(r">> ([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})(:.*)?", line)
        if not m:
            return None

        srcdst = m.group(1)
        opcode = m.group(2)
        raw = m.group(0)

        return {
            "src": srcdst[0],
            "dst": srcdst[1],
            "opcode": opcode,
            "raw": raw
        }


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CECAnalyzer()
    win.show()
    sys.exit(app.exec())
