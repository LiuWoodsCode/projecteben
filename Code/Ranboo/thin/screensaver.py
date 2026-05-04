import sys
import requests
from PySide6.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout
from PySide6.QtGui import QPixmap, QFont
from PySide6.QtCore import Qt, QTimer, QTime, QDate, QLocale


class SlideshowApp(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Slideshow Screensaver")
        self.setMouseTracking(True)

        # Image display
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black;")

        font = QFont()
        font.setPointSize(45)

        fonta = QFont()
        fonta.setPointSize(20)

        font1 = QFont()
        font1.setPointSize(18)

        font1a = QFont()
        font1a.setPointSize(12)


        # Bottom vignette
        self.bottom_vignette = QWidget(self)
        self.bottom_vignette.setStyleSheet("""
            background: qlineargradient(
                x1: 0, y1: 0, x2: 0, y2: 1,
                stop: 0 rgba(0, 0, 0, 0),
                stop: 1 rgba(0, 0, 0, 170)
            );
        """)

        # Bottom clock overlay without background
        self.bottom_clock_panel = QWidget(self)
        self.bottom_clock_panel.setStyleSheet("background: transparent;")

        bottom_clock_layout = QVBoxLayout(self.bottom_clock_panel)
        bottom_clock_layout.setContentsMargins(16, 14, 16, 14)
        bottom_clock_layout.setSpacing(4)

        self.bottom_time_label = QLabel(self.bottom_clock_panel)
        self.bottom_time_label.setAttribute(Qt.WA_TranslucentBackground, True)
        self.bottom_time_label.setStyleSheet("color: white; background: transparent; border: none;")
        self.bottom_time_label.setFont(font)

        self.bottom_date_label = QLabel(self.bottom_clock_panel)
        self.bottom_date_label.setAttribute(Qt.WA_TranslucentBackground, True)
        self.bottom_date_label.setStyleSheet("color: white; background: transparent; border: none;")
        self.bottom_date_label.setFont(font1)

        bottom_clock_layout.addWidget(self.bottom_time_label)
        bottom_clock_layout.addWidget(self.bottom_date_label)

        # Bottom-right info block
        self.bottom_info_panel = QWidget(self)
        self.bottom_info_panel.setStyleSheet("background: transparent;")

        bottom_info_layout = QVBoxLayout(self.bottom_info_panel)
        bottom_info_layout.setContentsMargins(16, 14, 16, 14)
        bottom_info_layout.setSpacing(2)
        bottom_info_layout.setAlignment(Qt.AlignRight | Qt.AlignBottom)

        self.start_label = QLabel("Press any key to get started", self.bottom_info_panel)
        self.start_label.setAttribute(Qt.WA_TranslucentBackground, True)
        self.start_label.setStyleSheet("color: white; background: transparent; border: none;")
        self.start_label.setFont(fonta)
        self.start_label.setAlignment(Qt.AlignRight)

        self.version_label = QLabel("Project Eben thin client, version 0.1", self.bottom_info_panel)
        self.version_label.setAttribute(Qt.WA_TranslucentBackground, True)
        self.version_label.setStyleSheet("color: white; background: transparent; border: none;")
        self.version_label.setFont(font1a)
        self.version_label.setAlignment(Qt.AlignRight)

        bottom_info_layout.addWidget(self.start_label)
        bottom_info_layout.addWidget(self.version_label)

        # Layout manually (simpler for fullscreen overlay)
        self.image_label.setGeometry(0, 0, self.width(), self.height())
        # Timers
        self.image_timer = QTimer()
        self.image_timer.timeout.connect(self.load_new_image)
        self.image_timer.start(10000)  # Change image every 10 seconds

        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_time)
        self.clock_timer.start(1000)

        # Initial load
        self.load_new_image()
        self.update_time()

    def resizeEvent(self, event):
        self.image_label.setGeometry(0, 0, self.width(), self.height())
        self.bottom_vignette.setGeometry(0, self.height() - 280, self.width(), 280)
        super().resizeEvent(event)

    def _position_clock_panels(self):
        self.bottom_info_panel.adjustSize()
        self.bottom_clock_panel.adjustSize()

        bottom_margin = 20
        self.bottom_clock_panel.move(20, self.height() - self.bottom_clock_panel.height() - bottom_margin)
        self.bottom_info_panel.move(
            self.width() - self.bottom_info_panel.width() - 20,
            self.height() - self.bottom_info_panel.height() - bottom_margin,
        )
        self.bottom_vignette.raise_()
        self.bottom_clock_panel.raise_()
        self.bottom_info_panel.raise_()

    def update_time(self):
        locale = QLocale.system()
        current_time = locale.toString(QTime.currentTime(), QLocale.ShortFormat)
        current_date = locale.toString(QDate.currentDate(), QLocale.LongFormat)

        self.bottom_time_label.setText(current_time)
        self.bottom_date_label.setText(current_date)
        self.bottom_time_label.adjustSize()
        self.bottom_date_label.adjustSize()
        self._position_clock_panels()

    def load_new_image(self):
        try:
            # Random image source (no API key needed)
            url = "https://picsum.photos/1920/1080"

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            pixmap = QPixmap()
            pixmap.loadFromData(response.content)

            # Scale to fit screen
            scaled = pixmap.scaled(
                self.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation
            )

            self.image_label.setPixmap(scaled)

        except Exception as e:
            print(f"Failed to load image: {e}")

    def keyPressEvent(self, event):
        # Exit on ESC (like a screensaver)
        if event.key() == Qt.Key_Escape:
            self.close()

    def mousePressEvent(self, event):
        self.close()

    def mouseMoveEvent(self, event):
        self.close()

    def wheelEvent(self, event):
        self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SlideshowApp()
    window.showFullScreen()
    sys.exit(app.exec())