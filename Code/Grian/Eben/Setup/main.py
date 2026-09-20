import sys
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLineEdit, QCheckBox, QFrame
)
from PySide6.QtGui import QGuiApplication, QPixmap, QImage
from PySide6.QtCore import Qt, Signal, QTimer

try:
    from PIL import Image, ImageFilter
except ImportError:
    Image = None
    ImageFilter = None


class BlurredBackground(QLabel):
    def __init__(self, image_path):
        super().__init__()
        # Resolve path relative to this script to avoid cwd issues
        base = os.path.dirname(__file__)
        full_path = os.path.join(base, image_path)
        pixmap = QPixmap(full_path)

        if pixmap.isNull():
            # Warn and create a neutral placeholder pixmap so the background isn't black
            print(f"Warning: could not load background image at {full_path}")
            pixmap = QPixmap(1280, 720)
            pixmap.fill(Qt.black)

        # keep an original pixmap for high-quality scaling on resize
        self._orig_pixmap = pixmap
        self.setPixmap(pixmap)
        self.setScaledContents(True)

    def blurred_for_size(self, size):
        if not self._orig_pixmap or self._orig_pixmap.isNull():
            return QPixmap()

        # Fast gaussian-like blur approximation:
        # downscale strongly, then upscale with smooth filtering.
        w = max(1, size.width())
        h = max(1, size.height())
        small_w = max(1, w // 14)
        small_h = max(1, h // 14)

        base = self._orig_pixmap.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if Image is None or ImageFilter is None:
            tiny = base.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            return tiny.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        qimg = base.toImage().convertToFormat(QImage.Format_RGBA8888)
        ptr = qimg.bits()
        raw = ptr.tobytes()
        pil = Image.frombytes("RGBA", (qimg.width(), qimg.height()), raw)
        pil = pil.resize((small_w, small_h), Image.Resampling.BILINEAR)
        pil = pil.filter(ImageFilter.GaussianBlur(radius=2.5))
        pil = pil.resize((w, h), Image.Resampling.BILINEAR)

        out = pil.tobytes("raw", "RGBA")
        out_qimg = QImage(out, w, h, 4 * w, QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(out_qimg)


class Container(QFrame):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 235);
                border-radius: 18px;
            }
        """)
        self.setFixedWidth(720)
        self.setFixedHeight(640)


class BasePage(QWidget):
    """Base class for pages with navigation buttons"""
    next_clicked = Signal()
    back_clicked = Signal()

    def __init__(self, show_back=False, show_next=True, next_text="Continue"):
        super().__init__()
        self.show_back = show_back
        self.show_next = show_next
        self.next_text = next_text

    def create_nav_buttons(self):
        """Creates and returns back/next buttons with layout"""
        self.back_btn = QPushButton("Back")
        self.next_btn = QPushButton(self.next_text)

        self.back_btn.clicked.connect(self.back_clicked.emit)
        self.next_btn.clicked.connect(self.next_clicked.emit)

        nav = QHBoxLayout()
        nav.setContentsMargins(0, 20, 0, 0)
        nav.setSpacing(12)
        
        if self.show_back:
            nav.addWidget(self.back_btn)
        else:
            nav.addStretch()

        nav.addStretch()

        if self.show_next:
            nav.addWidget(self.next_btn)

        return nav

    def update_button_text(self, is_last=False):
        """Update button text for final page"""
        if is_last:
            self.next_btn.setText("Finish")
        else:
            self.next_btn.setText(self.next_text)


class SetupWizard(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Linux Setup Wizard")
        self.setMinimumWidth(640)
        self.setMinimumHeight(480)
        # Don't show/resize yet — wait until the widget is fully constructed

        # Background
        self.bg = BlurredBackground("background.jpg")

        # Stack
        self.stack = QStackedWidget()

        self.hello = self.hello_page()
        self.user = self.user_page()
        self.system = self.system_page()
        self.summary = self.summary_page()

        self.stack.addWidget(self.hello)
        self.stack.addWidget(self.user)
        self.stack.addWidget(self.system)
        self.stack.addWidget(self.summary)

        # Connect page signals to navigation
        self.hello.next_clicked.connect(self.go_next)
        self.user.next_clicked.connect(self.go_next)
        self.user.back_clicked.connect(self.go_back)
        self.system.next_clicked.connect(self.go_next)
        self.system.back_clicked.connect(self.go_back)
        self.summary.next_clicked.connect(self.go_next)
        self.summary.back_clicked.connect(self.go_back)
        
        # Set focus when stack changes
        self.stack.currentChanged.connect(self.on_page_changed)

        content_layout = QVBoxLayout()
        content_layout.addWidget(self.stack)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Overlay container (transparent) - will be placed on top of the blurred background
        overlay = QWidget()
        overlay.setLayout(content_layout)
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        overlay.setStyleSheet("background: transparent;")

        # Make background and overlay children of this widget so they can overlap.
        # We'll manage sizing in resizeEvent so the background fills the window
        # and the overlay stays centered above it.
        self.bg.setParent(self)
        overlay.setParent(self)
        self._overlay = overlay

        # Keep an empty layout on the main window (margins handled by overlay)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    # 🌟 Hello screen (no container, key press to continue)
    def hello_page(self):
        class HelloPage(BasePage):
            def __init__(self):
                super().__init__(show_back=False, show_next=False)
                self.key_pressed = False
                self.setFocusPolicy(Qt.StrongFocus)
                
                layout = QVBoxLayout()
                layout.setContentsMargins(0, 0, 0, 0)
                
                label = QLabel("Hello")
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet("""
                    color: white;
                    font-size: 72px;
                    font-weight: 300;
                    letter-spacing: 2px;
                """)
                
                hint = QLabel("Press any key to continue")
                hint.setAlignment(Qt.AlignCenter)
                hint.setStyleSheet("""
                    font-size: 16px;
                    color: rgba(255, 255, 255, 160);
                    font-weight: 300;
                    letter-spacing: 0.5px;
                """)
                
                layout.addStretch()
                layout.addWidget(label)
                layout.addSpacing(20)
                layout.addWidget(hint)
                layout.addStretch()
                self.setLayout(layout)
                
            def keyPressEvent(self, event):
                if not self.key_pressed and not event.isAutoRepeat():
                    self.key_pressed = True
                    self.next_clicked.emit()

        page = HelloPage()
        page.setFocus()
        return page

    # 📦 User page
    def user_page(self):
        class UserPage(BasePage):
            def __init__(self, wizard_ref):
                super().__init__(show_back=False, show_next=True)
                self.wizard_ref = wizard_ref
                
                container = Container()
                layout = QVBoxLayout()
                layout.setContentsMargins(50, 40, 50, 40)
                layout.setSpacing(16)

                title = QLabel("Create your user")
                title.setStyleSheet("""
                    color: #333333;
                    font-size: 18px;
                    font-weight: 600;
                    letter-spacing: 0.3px;
                """)

                self.username = QLineEdit()
                self.username.setPlaceholderText("Username")
                self.username.setMinimumHeight(44)
                self.username.textChanged.connect(self.on_username_changed)

                layout.addWidget(title)
                layout.addWidget(self.username)
                layout.addStretch()
                
                # Add buttons inside container
                nav = self.create_nav_buttons()
                layout.addLayout(nav)

                container.setLayout(layout)

                wrapper = QVBoxLayout()
                wrapper.addStretch()
                wrapper.addWidget(container, alignment=Qt.AlignCenter)
                wrapper.addStretch()
                wrapper.setContentsMargins(64, 60, 64, 60)

                self.setLayout(wrapper)
                
                # Disable continue button until username is entered
                self.next_btn.setEnabled(False)
            
            def on_username_changed(self):
                """Enable Continue button only if username is not empty"""
                self.next_btn.setEnabled(bool(self.username.text().strip()))
        
        page = UserPage(self)
        self.username_input = page.username
        return page

    # 📦 System page
    def system_page(self):
        class SystemPage(BasePage):
            def __init__(self, wizard_ref):
                super().__init__(show_back=True, show_next=True)
                self.wizard_ref = wizard_ref
                
                container = Container()
                layout = QVBoxLayout()
                layout.setContentsMargins(50, 40, 50, 40)
                layout.setSpacing(16)

                title = QLabel("System Settings")
                title.setStyleSheet("""
                    color: #333333;
                    font-size: 18px;
                    font-weight: 600;
                    letter-spacing: 0.3px;
                """)

                self.updates = QCheckBox("Enable automatic updates")
                self.drivers = QCheckBox("Install proprietary drivers")
                
                for checkbox in [self.updates, self.drivers]:
                    checkbox.setFixedHeight(44)

                layout.addWidget(title)
                layout.addWidget(self.updates)
                layout.addWidget(self.drivers)
                layout.addStretch()
                
                # Add buttons inside container
                nav = self.create_nav_buttons()
                layout.addLayout(nav)

                container.setLayout(layout)

                wrapper = QVBoxLayout()
                wrapper.addStretch()
                wrapper.addWidget(container, alignment=Qt.AlignCenter)
                wrapper.addStretch()
                wrapper.setContentsMargins(64, 60, 64, 60)

                self.setLayout(wrapper)
        
        page = SystemPage(self)
        self.updates_check = page.updates
        self.drivers_check = page.drivers
        return page

    # 📦 Summary page
    def summary_page(self):
        class SummaryPage(BasePage):
            def __init__(self, wizard_ref):
                super().__init__(show_back=True, show_next=True, next_text="Finish")
                self.wizard_ref = wizard_ref
                
                container = Container()
                layout = QVBoxLayout()
                layout.setContentsMargins(50, 40, 50, 40)
                layout.setSpacing(16)

                title = QLabel("Summary")
                title.setStyleSheet("""
                    color: #333333;
                    font-size: 18px;
                    font-weight: 600;
                    letter-spacing: 0.3px;
                """)

                self.summary = QLabel()
                self.summary.setAlignment(Qt.AlignCenter)
                self.summary.setStyleSheet("""
                    color: #555555;
                    font-size: 15px;
                    line-height: 1.6;
                """)

                layout.addWidget(title)
                layout.addWidget(self.summary)
                layout.addStretch()
                
                # Add buttons inside container
                nav = self.create_nav_buttons()
                layout.addLayout(nav)

                container.setLayout(layout)

                wrapper = QVBoxLayout()
                wrapper.addStretch()
                wrapper.addWidget(container, alignment=Qt.AlignCenter)
                wrapper.addStretch()
                wrapper.setContentsMargins(64, 60, 64, 60)

                self.setLayout(wrapper)
        
        page = SummaryPage(self)
        self.summary_label = page.summary
        return page

    # 🔁 Navigation
    def on_page_changed(self):
        """Set focus to current page"""
        current = self.stack.currentWidget()
        if current:
            current.setFocus()
    
    def go_next(self):
        i = self.stack.currentIndex()

        if i == self.stack.count() - 1:
            QApplication.quit()
            return

        if i == self.stack.count() - 2:
            self.update_summary()

        self.stack.setCurrentIndex(i + 1)

    def go_back(self):
        i = self.stack.currentIndex()
        if i > 0:
            self.stack.setCurrentIndex(i - 1)

    def update_summary(self):
        user = self.username_input.text() or "Not set"
        updates = "Yes" if self.updates_check.isChecked() else "No"
        drivers = "Yes" if self.drivers_check.isChecked() else "No"

        self.summary_label.setText(
            f"Username: {user}\nUpdates: {updates}\nDrivers: {drivers}"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Render a visibly blurred background for the current window size
        blurred = self.bg.blurred_for_size(self.size())
        if not blurred.isNull():
            self.bg.setPixmap(blurred)
        self.bg.setGeometry(0, 0, self.width(), self.height())
        # Make overlay cover the full window so its internal layout can center content
        if hasattr(self, '_overlay') and self._overlay is not None:
            self._overlay.setGeometry(0, 0, self.width(), self.height())

    def keyPressEvent(self, event):
        """Forward key press events to the current page"""
        current = self.stack.currentWidget()
        if current and hasattr(current, 'keyPressEvent'):
            current.keyPressEvent(event)
        super().keyPressEvent(event)


if __name__ == "__main__":
    # Prevent Qt from loading the platform theme plugin (e.g. qt6ct on RPi OS)
    os.environ.pop('QT_QPA_PLATFORMTHEME', None)

    app = QApplication(sys.argv)
    # Force a neutral, cross-platform style so the system theme is ignored
    app.setStyle('Fusion')
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyleSheet("""
    QWidget { background: transparent; }
    """)

    win = SetupWizard()
    # On macOS, changing to full-screen before Cocoa has given the content view
    # a non-zero frame produces an "invalid window content view size" warning.
    # Showing first and deferring the state change by one event-loop turn gives
    # the native window valid geometry while keeping the full-screen behavior.
    win.show()
    QTimer.singleShot(0, win.showFullScreen)
    sys.exit(app.exec())
