import sys
import os
import json
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from PySide6.QtCore import QEvent, QUrl, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


WEB_ROOT = os.path.abspath(".")


def relaunch_under_x_if_needed():
    qt_platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY")

    if qt_platform == "xcb":
        return

    if not wayland_display and xdg_session_type != "wayland":
        return

    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Wayland session detected, but no X display is available for relaunch")

    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "xcb"
    env.pop("WAYLAND_DISPLAY", None)

    os.execvpe(sys.executable, [sys.executable, *sys.argv], env)


class MessageBoxPage(QWebEnginePage):
    def javaScriptAlert(self, securityOrigin, message):
        QMessageBox.critical(None, "EbenVnc", message)


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):
        pass


def start_http_server():
    # Bind to port 0 → OS picks a free port
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: QuietHandler(*args, directory=WEB_ROOT, **kwargs)
    )

    port = server.server_address[1]
    print(f"port is now {port}")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"server is running")

    return server, port


class MainWindow(QMainWindow):
    def __init__(self, port):
        super().__init__()

        self.setWindowTitle("EbenVnc")

        self.page = MessageBoxPage(self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.view.installEventFilter(self)
        self.view.loadFinished.connect(self._sync_remote_focus)
        self.view.setUrl(QUrl(f"http://127.0.0.1:{port}/vnc_lite.html?host=10.42.1.1&port=5900"))
        self.setCentralWidget(self.view)

        tools_menu = self.menuBar().addMenu("Tools")
        devtools_action = QAction("Open DevTools", self)
        devtools_action.triggered.connect(self.open_devtools)
        tools_menu.addAction(devtools_action)

        view_menu = self.menuBar().addMenu("View")
        self.fullscreen_action = QAction("Fullscreen", self)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        self.devtools_view = None
        self.keyboard_grabbed = False

    def _run_page_hook(self, script):
        self.view.page().runJavaScript(script)

    def _should_forward_keyboard(self, obj):
        return obj is self.view and self.isActiveWindow() and self.view.hasFocus()

    def _qt_key_to_dom_code(self, event):
        key = event.key()

        if Qt.Key_A <= key <= Qt.Key_Z:
            return f"Key{chr(key)}"
        if Qt.Key_0 <= key <= Qt.Key_9:
            return f"Digit{chr(key)}"

        key_map = {
            Qt.Key_Space: "Space",
            Qt.Key_Enter: "Enter",
            Qt.Key_Return: "Enter",
            Qt.Key_Tab: "Tab",
            Qt.Key_Backtab: "Tab",
            Qt.Key_Backspace: "Backspace",
            Qt.Key_Escape: "Escape",
            Qt.Key_Insert: "Insert",
            Qt.Key_Delete: "Delete",
            Qt.Key_Home: "Home",
            Qt.Key_End: "End",
            Qt.Key_PageUp: "PageUp",
            Qt.Key_PageDown: "PageDown",
            Qt.Key_Left: "ArrowLeft",
            Qt.Key_Right: "ArrowRight",
            Qt.Key_Up: "ArrowUp",
            Qt.Key_Down: "ArrowDown",
            Qt.Key_Shift: "ShiftLeft",
            Qt.Key_Control: "ControlLeft",
            Qt.Key_Alt: "AltLeft",
            Qt.Key_Meta: "MetaLeft",
            Qt.Key_CapsLock: "CapsLock",
            Qt.Key_NumLock: "NumLock",
            Qt.Key_ScrollLock: "ScrollLock",
            Qt.Key_Pause: "Pause",
            Qt.Key_Print: "PrintScreen",
            Qt.Key_Menu: "ContextMenu",
            Qt.Key_F1: "F1",
            Qt.Key_F2: "F2",
            Qt.Key_F3: "F3",
            Qt.Key_F4: "F4",
            Qt.Key_F5: "F5",
            Qt.Key_F6: "F6",
            Qt.Key_F7: "F7",
            Qt.Key_F8: "F8",
            Qt.Key_F9: "F9",
            Qt.Key_F10: "F10",
            Qt.Key_F11: "F11",
            Qt.Key_F12: "F12",
            Qt.Key_F13: "F13",
            Qt.Key_F14: "F14",
            Qt.Key_F15: "F15",
            Qt.Key_F16: "F16",
            Qt.Key_F17: "F17",
            Qt.Key_F18: "F18",
            Qt.Key_F19: "F19",
            Qt.Key_F20: "F20",
            Qt.Key_F21: "F21",
            Qt.Key_F22: "F22",
            Qt.Key_F23: "F23",
            Qt.Key_F24: "F24",
            Qt.Key_F25: "F25",
            Qt.Key_F26: "F26",
            Qt.Key_F27: "F27",
            Qt.Key_F28: "F28",
            Qt.Key_F29: "F29",
            Qt.Key_F30: "F30",
            Qt.Key_F31: "F31",
            Qt.Key_F32: "F32",
            Qt.Key_F33: "F33",
            Qt.Key_F34: "F34",
            Qt.Key_F35: "F35",
            Qt.Key_Semicolon: "Semicolon",
            Qt.Key_Equal: "Equal",
            Qt.Key_Comma: "Comma",
            Qt.Key_Minus: "Minus",
            Qt.Key_Period: "Period",
            Qt.Key_Slash: "Slash",
            Qt.Key_Backslash: "Backslash",
            Qt.Key_BracketLeft: "BracketLeft",
            Qt.Key_BracketRight: "BracketRight",
            Qt.Key_Apostrophe: "Quote",
            Qt.Key_QuoteLeft: "Backquote",
        }

        return key_map.get(key, "Unidentified")

    def _qt_key_to_dom_key(self, event):
        text = event.text()

        if text and len(text) == 1 and not text.isspace():
            return text

        key = event.key()
        key_map = {
            Qt.Key_Space: " ",
            Qt.Key_Enter: "Enter",
            Qt.Key_Return: "Enter",
            Qt.Key_Tab: "Tab",
            Qt.Key_Backtab: "Tab",
            Qt.Key_Backspace: "Backspace",
            Qt.Key_Escape: "Escape",
            Qt.Key_Insert: "Insert",
            Qt.Key_Delete: "Delete",
            Qt.Key_Home: "Home",
            Qt.Key_End: "End",
            Qt.Key_PageUp: "PageUp",
            Qt.Key_PageDown: "PageDown",
            Qt.Key_Left: "ArrowLeft",
            Qt.Key_Right: "ArrowRight",
            Qt.Key_Up: "ArrowUp",
            Qt.Key_Down: "ArrowDown",
            Qt.Key_Shift: "Shift",
            Qt.Key_Control: "Control",
            Qt.Key_Alt: "Alt",
            Qt.Key_Meta: "Meta",
            Qt.Key_CapsLock: "CapsLock",
            Qt.Key_NumLock: "NumLock",
            Qt.Key_ScrollLock: "ScrollLock",
            Qt.Key_Pause: "Pause",
            Qt.Key_Print: "PrintScreen",
            Qt.Key_Menu: "ContextMenu",
            Qt.Key_F1: "F1",
            Qt.Key_F2: "F2",
            Qt.Key_F3: "F3",
            Qt.Key_F4: "F4",
            Qt.Key_F5: "F5",
            Qt.Key_F6: "F6",
            Qt.Key_F7: "F7",
            Qt.Key_F8: "F8",
            Qt.Key_F9: "F9",
            Qt.Key_F10: "F10",
            Qt.Key_F11: "F11",
            Qt.Key_F12: "F12",
            Qt.Key_F13: "F13",
            Qt.Key_F14: "F14",
            Qt.Key_F15: "F15",
            Qt.Key_F16: "F16",
            Qt.Key_F17: "F17",
            Qt.Key_F18: "F18",
            Qt.Key_F19: "F19",
            Qt.Key_F20: "F20",
            Qt.Key_F21: "F21",
            Qt.Key_F22: "F22",
            Qt.Key_F23: "F23",
            Qt.Key_F24: "F24",
            Qt.Key_F25: "F25",
            Qt.Key_F26: "F26",
            Qt.Key_F27: "F27",
            Qt.Key_F28: "F28",
            Qt.Key_F29: "F29",
            Qt.Key_F30: "F30",
            Qt.Key_F31: "F31",
            Qt.Key_F32: "F32",
            Qt.Key_F33: "F33",
            Qt.Key_F34: "F34",
            Qt.Key_F35: "F35",
            Qt.Key_Semicolon: ";",
            Qt.Key_Equal: "=",
            Qt.Key_Comma: ",",
            Qt.Key_Minus: "-",
            Qt.Key_Period: ".",
            Qt.Key_Slash: "/",
            Qt.Key_Backslash: "\\",
            Qt.Key_BracketLeft: "[",
            Qt.Key_BracketRight: "]",
            Qt.Key_Apostrophe: "'",
            Qt.Key_QuoteLeft: "`",
        }

        if key in key_map:
            return key_map[key]

        return text if text else "Unidentified"

    def _forward_keyboard_event(self, event, pressed):
        payload = {
            "type": "keydown" if pressed else "keyup",
            "code": self._qt_key_to_dom_code(event),
            "key": self._qt_key_to_dom_key(event),
            "location": int(event.key() in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta)),
            "repeat": bool(event.isAutoRepeat()),
            "ctrlKey": bool(event.modifiers() & Qt.ControlModifier),
            "altKey": bool(event.modifiers() & Qt.AltModifier),
            "shiftKey": bool(event.modifiers() & Qt.ShiftModifier),
            "metaKey": bool(event.modifiers() & Qt.MetaModifier),
        }
        self._run_page_hook(f"window.EbenVnc && window.EbenVnc.forwardKeyboardEvent && window.EbenVnc.forwardKeyboardEvent({json.dumps(payload)});")

    def toggle_fullscreen(self, checked=None):
        should_fullscreen = bool(checked) if checked is not None else not self.isFullScreen()

        if should_fullscreen:
            self.showFullScreen()
            self.fullscreen_action.setChecked(True)
        else:
            self.showNormal()
            self.fullscreen_action.setChecked(False)

    def _sync_remote_focus(self, *_args):
        if self.isActiveWindow():
            self.view.setFocus()
            if not self.keyboard_grabbed:
                self.view.grabKeyboard()
                self.keyboard_grabbed = True
            self._run_page_hook("window.EbenVnc && window.EbenVnc.requestFocus && window.EbenVnc.requestFocus();")
        else:
            if self.keyboard_grabbed:
                self.view.releaseKeyboard()
                self.keyboard_grabbed = False
            self._run_page_hook("window.EbenVnc && window.EbenVnc.releaseFocus && window.EbenVnc.releaseFocus();")

    def eventFilter(self, obj, event):
        if not self._should_forward_keyboard(obj):
            return super().eventFilter(obj, event)

        if event.type() == QEvent.Type.KeyPress:
            if (self.isFullScreen() and
                event.key() == Qt.Key_F and
                (event.modifiers() & Qt.ControlModifier) and
                (event.modifiers() & Qt.AltModifier)):
                event.accept()
                self.toggle_fullscreen(False)
                return True

        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True

        if event.type() == QEvent.Type.KeyPress:
            event.accept()
            self._forward_keyboard_event(event, True)
            return True

        if event.type() == QEvent.Type.KeyRelease:
            event.accept()
            self._forward_keyboard_event(event, False)
            return True

        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        super().changeEvent(event)

        if event.type() == QEvent.Type.ActivationChange:
            self._sync_remote_focus()

        if event.type() == QEvent.Type.WindowStateChange:
            self.fullscreen_action.setChecked(self.isFullScreen())

    def open_devtools(self):
        if self.devtools_view is None:
            self.devtools_view = QWebEngineView()
            self.devtools_view.setWindowTitle("DevTools")
            self.devtools_view.resize(1200, 800)
            self.view.page().setDevToolsPage(self.devtools_view.page())

        self.devtools_view.show()
        self.devtools_view.raise_()
        self.devtools_view.activateWindow()

    def closeEvent(self, event):
        if self.keyboard_grabbed:
            self.view.releaseKeyboard()
            self.keyboard_grabbed = False

        super().closeEvent(event)


def main():
    if not os.path.isdir(WEB_ROOT):
        raise FileNotFoundError(f"Missing web root: {WEB_ROOT}")

    relaunch_under_x_if_needed()

    server, port = start_http_server()

    app = QApplication(sys.argv)

    window = MainWindow(port)
    window.resize(1000, 700)
    window.show()

    exit_code = app.exec()

    server.shutdown()
    server.server_close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()