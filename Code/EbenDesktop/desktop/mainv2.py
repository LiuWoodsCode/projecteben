import sys
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QColor, QFont, QPalette
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
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


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


class DevicePanel(QWidget):
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

        self.add_device("Sayori’s Raspberry Pi 5", "Raspberry Pi 5 Model B Rev 1.0")
        self.add_device("Monika’s Raspberry...", "Raspberry Pi 3 Model B+")
        self.add_device("Yuri’s Raspberry Pi 4", "Unavailable", unavailable=True)

        self.list_widget.setCurrentRow(0)

    def add_device(self, name: str, subtitle: str, unavailable: bool = False):
        item = QListWidgetItem()
        widget = DeviceListItem(name, subtitle, unavailable=unavailable)

        item.setSizeHint(QSize(260, 64))
        if unavailable:
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)

        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)


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

        header_layout.addWidget(self.title_label)
        header_layout.addStretch()

        self.content = QFrame()
        self.content.setObjectName("detailContent")
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root.addWidget(self.header)
        root.addWidget(self.content, 1)


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

        splitter.addWidget(self.device_panel)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 830])

        frame_layout.addWidget(splitter)
        outer_layout.addWidget(outer_frame)

        self.device_panel.list_widget.currentRowChanged.connect(self.on_device_changed)

    def _create_menu(self):
        menu_bar = QMenuBar(self)
        self.setMenuBar(menu_bar)

        file_menu = menu_bar.addMenu("File")
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def on_device_changed(self, row: int):
        titles = [
            "Sayori’s Raspberry Pi 5",
            "Monika’s Raspberry...",
            "Yuri’s Raspberry Pi 4",
        ]
        if 0 <= row < len(titles):
            self.detail_panel.title_label.setText(titles[row])


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
    app = QApplication(sys.argv)
    app.setApplicationName("Eben Desktop")

    apply_dark_palette(app)
    apply_styles(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()