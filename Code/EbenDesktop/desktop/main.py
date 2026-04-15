import argparse
import sys
import os
import json
import threading
from datetime import datetime
from functools import partial
from time import sleep
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from PySide6.QtCore import QEvent, QUrl, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMainWindow, QMessageBox
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from eben_api import EbenApi


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
    def __init__(self, port: int, app: QApplication, host: str, force_no_global_menu: bool = False):
        super().__init__()

        self.app = app
        self.port = port
        self.host = host
        self.setWindowTitle("Project Eben")
        self.api = EbenApi(base_url=f"http://{host}:8000")

        self.page = MessageBoxPage(self)
        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.view.setFocusPolicy(Qt.StrongFocus)
        self.view.installEventFilter(self)
        self.view.loadFinished.connect(self._sync_remote_focus)
        self.view.setUrl(QUrl(f"http://127.0.0.1:{port}/vnc_lite.html?host={self.host}&port=5900"))
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.NavigateOnDropEnabled, False)
        QWebEngineSettings.setAttribute(self.view.settings(), QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, False)
        self.setCentralWidget(self.view)

        menu_bar = self.menuBar()
        if force_no_global_menu:
            menu_bar.setNativeMenuBar(False)

        tools_menu = menu_bar.addMenu("Tools")
        devtools_action = QAction("Open DevTools", self)
        devtools_action.triggered.connect(self.open_devtools)
        tools_menu.addAction(devtools_action)
        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_page)
        tools_menu.addAction(refresh_action)

        device_menu = menu_bar.addMenu("Device")
        self._add_api_action(device_menu, "Model", partial(self._api_get_text, "/device/model", "Device Model"))
        self._add_api_action(device_menu, "Serial", partial(self._api_get_text, "/device/serial", "Device Serial"))
        self._add_api_action(device_menu, "Revision", partial(self._api_get_text, "/device/revision", "Device Revision"))
        self._add_api_action(device_menu, "Hostname", partial(self._api_get_text, "/device/hostname", "Hostname"))
        self._add_api_action(device_menu, "Kernel Cmdline", partial(self._api_get_text, "/kernel/cmdline", "Kernel Cmdline"))
        self._add_api_action(device_menu, "Kernel Version", partial(self._api_get_text, "/kernel/version", "Kernel Version"))
        self._add_api_action(device_menu, "Uptime", partial(self._api_get_text, "/session/uptime", "Uptime"))
        self._add_api_action(device_menu, "Disk Usage", partial(self._api_get_json, "/device/resource/disk", "Disk Usage"))
        self._add_api_action(device_menu, "Memory Usage", partial(self._api_get_json, "/device/resource/mem", "Memory Usage"))

        vnc_menu = menu_bar.addMenu("VNC")
        self._add_api_action(vnc_menu, "Start wayvnc", partial(self._api_post_json, "/vnc/start", "Start wayvnc"))
        self._add_api_action(vnc_menu, "Start wayvnc websocket", partial(self._api_post_json, "/vnc/start/websocket", "Start wayvnc websocket"))
        self._add_api_action(vnc_menu, "Start wayvnc + noVNC", partial(self._api_post_json, "/vnc/start/novnc", "Start wayvnc + noVNC"))
        self._add_api_action(vnc_menu, "Stop wayvnc", partial(self._api_post_json, "/vnc/stop", "Stop wayvnc"))
        self._add_api_action(vnc_menu, "Stop noVNC", partial(self._api_post_json, "/vnc/stop/novnc", "Stop noVNC"))

        power_menu = menu_bar.addMenu("Power")
        self._add_api_action(power_menu, "Restart", partial(self._confirm_and_post, "Restart", "This will reboot the system.", "/power/restart", "Restart"))
        self._add_api_action(power_menu, "Power off", partial(self._confirm_and_post, "Power off", "This will shut the system down.", "/power/poweroff", "Power off"))
        self._add_api_action(power_menu, "Sleep", partial(self._confirm_and_post, "Sleep", "This will suspend the system.", "/power/sleep", "Sleep"))
        self._add_api_action(power_menu, "Hibernate", partial(self._confirm_and_post, "Hibernate", "This will hibernate the system.", "/power/hibernate", "Hibernate"))

        settings_menu = menu_bar.addMenu("Settings")
        self._add_api_action(settings_menu, "Set Time...", self._set_system_time)
        self._add_api_action(settings_menu, "Sync Pi Clock to System Time", self._sync_pi_clock_to_system_time)

        files_menu = menu_bar.addMenu("Files")
        self._add_api_action(files_menu, "Upload File...", self._upload_file_via_dialog)
        self._add_api_action(files_menu, "Download File...", self._download_file_via_dialog)

        view_menu = menu_bar.addMenu("View")
        self.fullscreen_action = QAction("Fullscreen", self)
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        view_menu.addAction(self.fullscreen_action)

        help_menu = menu_bar.addMenu("Help")
        self._add_api_action(help_menu, "About Qt", self._about_qt)

        self.devtools_view = None
        self.keyboard_grabbed = False

    def _about_qt(self):
        self.app.aboutQt()

    def _add_api_action(self, menu, title, handler):
        action = QAction(title, self)
        action.triggered.connect(handler)
        menu.addAction(action)

    def _api_request(self, method, path, query=None, json_body=None, data=None):
        return self.api.request(method, path, query=query, json_body=json_body, data=data)

    def _show_message(self, title, text, detailed_text=None, icon=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        if detailed_text:
            box.setDetailedText(detailed_text)
        box.exec()

    def _show_api_error(self, title, error):
        self._show_message(title, "Request failed.", str(error), QMessageBox.Critical)

    def _show_api_result(self, title, response):
        body = response["text"].strip()
        if response["content_type"] == "application/json":
            try:
                body = json.dumps(json.loads(body), indent=2, sort_keys=True)
            except json.JSONDecodeError:
                pass

        summary = f"Status: {response['status']}\nContent-Type: {response['content_type']}"
        self._show_message(title, "Request succeeded.", f"{summary}\n\n{body}" if body else summary)

    def _api_get_text(self, path, title, checked=False):
        try:
            response = self._api_request("GET", path)
        except RuntimeError as error:
            self._show_api_error(title, error)
            return

        self._show_message(title, response["text"].strip() or "No response body.", f"Status: {response['status']}")

    def _api_get_json(self, path, title, checked=False):
        try:
            response = self._api_request("GET", path)
        except RuntimeError as error:
            self._show_api_error(title, error)
            return

        self._show_api_result(title, response)

    def _api_post_json(self, path, title, checked=False):
        try:
            response = self._api_request("POST", path)
        except RuntimeError as error:
            self._show_api_error(title, error)
            return

        self._show_api_result(title, response)

    def _confirm_and_post(self, dialog_title, dialog_text, path, title, checked=False):
        reply = QMessageBox.question(self, dialog_title, dialog_text, QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        try:
            response = self._api_request("GET", path)
        except RuntimeError as error:
            self._show_api_error(title, error)
            return

        self._show_api_result(title, response)

    def _set_system_time(self, checked=False):
        default_value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        value, accepted = QInputDialog.getText(
            self,
            "Set System Time",
            "Enter date and time (YYYY-MM-DD HH:MM:SS):",
            text=default_value,
        )
        if not accepted or not value.strip():
            return

        try:
            response = self._api_request("POST", "/settings/time", json_body={"datetime": value.strip()})
        except RuntimeError as error:
            self._show_api_error("Set System Time", error)
            return

        self._show_api_result("Set System Time", response)

    def _sync_pi_clock_to_system_time(self, checked=False):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            response = self._api_request("POST", "/settings/time", json_body={"datetime": current_time})
        except RuntimeError as error:
            self._show_api_error("Sync Pi Clock", error)
            return

        self._show_api_result("Sync Pi Clock", response)

    def _upload_file_via_dialog(self, checked=False):
        local_path, _ = QFileDialog.getOpenFileName(self, "Select File to Upload")
        if not local_path:
            return

        target_path, accepted = QInputDialog.getText(
            self,
            "Upload File",
            "Remote path under your home directory:",
            text=os.path.basename(local_path),
        )
        if not accepted or not target_path.strip():
            return

        with open(local_path, "rb") as source_file:
            payload = source_file.read()

        try:
            response = self._api_request(
                "POST",
                "/files/upload",
                query={"path": target_path.strip()},
                data=payload,
            )
        except RuntimeError as error:
            self._show_api_error("Upload File", error)
            return

        self._show_api_result("Upload File", response)

    def _download_file_via_dialog(self, checked=False):
        source_path, accepted = QInputDialog.getText(
            self,
            "Download File",
            "Remote path under your home directory:",
        )
        if not accepted or not source_path.strip():
            return

        local_path, _ = QFileDialog.getSaveFileName(self, "Save Downloaded File", os.path.basename(source_path.strip()))
        if not local_path:
            return

        try:
            response = self._api_request("GET", "/files/download", query={"path": source_path.strip()})
        except RuntimeError as error:
            self._show_api_error("Download File", error)
            return

        with open(local_path, "wb") as destination_file:
            destination_file.write(response["body"])

        self._show_message(
            "Download File",
            f"Saved to {local_path}",
            f"Status: {response['status']}\nContent-Type: {response['content_type']}\nBytes written: {len(response['body'])}",
        )


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
        print(f"Forwarding keyevent:\n{payload}\n")
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

    def refresh_page(self):
        self.view.setUrl(QUrl("about:blank"))
        sleep(1)
        self.view.setUrl(QUrl(f"http://127.0.0.1:{self.port}/vnc_lite.html?host={self.host}&port=5900"))

    def closeEvent(self, event):
        if self.keyboard_grabbed:
            self.view.releaseKeyboard()
            self.keyboard_grabbed = False

        super().closeEvent(event)
def main():
    if not os.path.isdir(WEB_ROOT):
        raise FileNotFoundError(f"Missing web root: {WEB_ROOT}")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--force-no-global-menu", action="store_true")
    parser.add_argument("--host", default="10.42.1.1")
    args, qt_args = parser.parse_known_args()

    relaunch_under_x_if_needed()

    if args.force_no_global_menu:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    server, port = start_http_server()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationDisplayName("Eben Desktop")
    app.setApplicationName("Eben Desktop")

    window = MainWindow(port, app, host=args.host, force_no_global_menu=args.force_no_global_menu)
    try:
        model = window.api.request("GET", "/device/model")
        hostname = window.api.request("GET", "/device/hostname")
    except Exception:
        model = "N/A"
        hostname = "N/A"
    title = f"{hostname['text'].strip()} ({model['text'].strip()})" if isinstance(model, dict) and isinstance(hostname, dict) else f"{hostname} ({model})"
    window.setWindowTitle(title)
    window.resize(1000, 700)
    window.show()

    exit_code = app.exec()

    server.shutdown()
    server.server_close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()