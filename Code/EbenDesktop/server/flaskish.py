from http.server import BaseHTTPRequestHandler, HTTPServer
import traceback
from urllib.parse import urlparse, parse_qs, unquote
import json
import os
import mimetypes


MAX_REQUEST_BODY_BYTES = 512 * 1024 * 1024


class RequestTooLargeError(Exception):
    pass


########################################
# Request Object
########################################
class Request:

    def __init__(self, handler, max_body_bytes=None):

        parsed = urlparse(handler.path)

        self.path = parsed.path
        self.method = handler.command
        self.headers = handler.headers

        self.query = {
            k: v[0] if len(v) == 1 else v
            for k, v in parse_qs(parsed.query).items()
        }

        self.body = None
        self.json = None

        if self.method in ["POST", "PUT", "PATCH"]:
            length = int(handler.headers.get("Content-Length", 0))
            if max_body_bytes is not None and length > max_body_bytes:
                raise RequestTooLargeError(
                    f"Request body exceeds limit of {max_body_bytes} bytes"
                )
            if length:
                raw = handler.rfile.read(length)
                self.body = raw
                try:
                    self.json = json.loads(raw)
                except:
                    pass


########################################
# Response Types
########################################
class Response:

    def __init__(self, body, status=200, content_type="text/plain", headers={}):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.headers = headers


class JsonResponse(Response):
    def __init__(self, obj, status=200, headers={}):
        super().__init__(
            json.dumps(obj).encode(),
            status,
            "application/json",
            headers
        )


class TextResponse(Response):
    def __init__(self, text, status=200, headers={}):
        super().__init__(
            text.encode("utf-8"),
            status,
            "text/plain",
            headers
        )


class BinaryResponse(Response):
    def __init__(self, data, content_type="application/octet-stream", status=200, headers={}):
        super().__init__(data, status, content_type, headers)

class BinaryResponseForProjectEbenFileDownload(Response):
    def __init__(self, data, content_type="application/octet-stream", status=200, filename="niko.bin"):
        super().__init__(data, status, content_type, filename)


class FileResponse(Response):
    def __init__(self, filepath, status=200):

        if not os.path.exists(filepath):
            raise FileNotFoundError(filepath)

        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"

        with open(filepath, "rb") as f:
            data = f.read()

        super().__init__(data, status, mime)


########################################
# App Core
########################################
class App:

    def __init__(self):
        self.routes = {"GET": {}, "POST": {}, "PUT": {}, "DELETE": {}}
        self.static_mounts = []
        self.max_request_body_bytes = MAX_REQUEST_BODY_BYTES

    ####################################
    # Routing
    ####################################
    def get(self, path):
        return self._route("GET", path)

    def post(self, path):
        return self._route("POST", path)

    def put(self, path):
        return self._route("PUT", path)

    def delete(self, path):
        return self._route("DELETE", path)

    def _route(self, method, path):
        def decorator(func):
            self.routes[method][path] = func
            return func
        return decorator

    ####################################
    # Static Mounts
    ####################################
    def mount(self, url_prefix, folder):
        self.static_mounts.append((url_prefix.rstrip("/"), os.path.abspath(folder)))

    ####################################
    # Handle Response Conversion
    ####################################
    def _normalize_response(self, res):

        if isinstance(res, Response):
            return res

        if isinstance(res, tuple):
            body, status = res
        else:
            body, status = res, 200

        if isinstance(body, dict):
            return JsonResponse(body, status)

        if isinstance(body, str):
            return TextResponse(body, status)

        if isinstance(body, bytes):
            return BinaryResponse(body, status=status)
        
        if isinstance(body, float):
            return TextResponse(str(body), status)
        
        if isinstance(body, int):
            return TextResponse(str(body), status)
        
        if isinstance(body, list):
            return TextResponse(str(body), status)

        raise TypeError(f"Unsupported response type (got {type(body).__name__})")

    ####################################
    # Run
    ####################################
    def run(self, host="0.0.0.0", port=8000):

        app = self

        class Handler(BaseHTTPRequestHandler):

            def _send(self, response):

                self.send_response(response.status)
                self.send_header("Content-Type", response.content_type)
                if response.filename:
                    # self.send_header("Content-Disposition", f"attachment; filename="{response.filename}")
                    print(hi)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()

                self.wfile.write(response.body)

            def _check_static(self, path):

                for prefix, folder in app.static_mounts:
                    if path.startswith(prefix):

                        rel = unquote(path[len(prefix):])
                        full = os.path.abspath(os.path.join(folder, rel.lstrip("/")))

                        # security guard: don't allow path escape
                        if not full.startswith(folder):
                            return Response(b"Forbidden", 403)

                        if os.path.isfile(full):
                            return FileResponse(full)

                return None

            def handle_req(self):
                print(self.__str__())
                parsed = urlparse(self.path)
                path = parsed.path

                # Static?
                static = self._check_static(path)
                if static:
                    self._send(static)
                    return

                try:
                    request = Request(self, max_body_bytes=app.max_request_body_bytes)
                except RequestTooLargeError:
                    self._send(TextResponse("Request body too large", 413))
                    return

                func = app.routes.get(request.method, {}).get(path)

                if not func:
                    self._send(TextResponse("Not Found", 404))
                    return

                try:
                    raw = func(request)
                    response = app._normalize_response(raw)
                    self._send(response)

                except Exception as e:
                    self._send(TextResponse(str(traceback.format_exc()), 500))

            def do_GET(self):
                self.handle_req()

            def do_POST(self):
                self.handle_req()

            def do_PUT(self):
                self.handle_req()

            def do_DELETE(self):
                self.handle_req()

            def log_message(self, *_):
                pass

        server = HTTPServer((host, port), Handler)
        print(f"API running http://{host}:{port}")
        server.serve_forever()
        