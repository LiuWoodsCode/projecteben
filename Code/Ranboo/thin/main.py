import sys
import os
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl

WEB_ROOT = os.path.abspath(".")


# --- Web server ---
class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):
        pass  # silence logs


def start_http_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),  # auto-pick port
        lambda *args, **kwargs: QuietHandler(*args, directory=WEB_ROOT, **kwargs)
    )

    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    print(f"Server running on http://127.0.0.1:{port}")
    return server, port


# --- Window + WebView ---
class MainWindow(QMainWindow):
    def __init__(self, port):
        super().__init__()

        self.setWindowTitle("Minimal WebView")

        self.view = QWebEngineView()
        self.view.setUrl(QUrl(f"http://127.0.0.1:{port}"))

        self.setCentralWidget(self.view)


# --- App entry ---
def main():
    if not os.path.isdir(WEB_ROOT):
        raise FileNotFoundError(f"Missing web root: {WEB_ROOT}")

    server, port = start_http_server()

    app = QApplication(sys.argv)
    window = MainWindow(port)

    window.showFullScreen()  # fullscreen like you wanted
    # or: window.show()

    exit_code = app.exec()

    server.shutdown()
    server.server_close()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()