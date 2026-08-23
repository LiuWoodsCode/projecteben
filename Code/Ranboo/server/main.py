import sys
from pathlib import Path
from dataclasses import asdict

from flaskish import App, BinaryResponse, TextResponse
import backend as backend
backend = backend.Api()
app = App()


def _get_file_path(req):
    """Extract a file path from the request.

    Request layout:
    - `req.json` may be a JSON object containing one of these keys:
      - `path`
      - `file`
      - `source`
      - `target`
      - `name`
    - If no JSON key is present, the same keys are checked in `req.query`.

    Return value:
    - `str` containing the first non-empty path-like value found.
    - `None` when no path value is provided.
    """
    if isinstance(req.json, dict):
        for key in ("path", "file", "source", "target", "name"):
            value = req.json.get(key)
            if value:
                return value

    for key in ("path", "file", "source", "target", "name"):
        value = req.query.get(key)
        if value:
            return value

    return None


def _webui_html():
    return TextResponse(
        """<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="/theme/style.css">
    <title>Ranboo Server</title>
</head>
<body>
    <h1>Ranboo Server</h1>
    <p>This is a prototype of the web UI and is not repersentive of the final product</p>
    <h2>Text data</h2>
    <div>
        <p>/device/model: <span id="device-model">loading...</span></p>
        <p>/device/serial: <span id="device-serial">loading...</span></p>
        <p>/device/revision: <span id="device-revision">loading...</span></p>
        <p>/kernel/cmdline: <span id="kernel-cmdline">loading...</span></p>
        <p>/kernel/version: <span id="kernel-version">loading...</span></p>
        <p>/device/hostname: <span id="device-hostname">loading...</span></p>
        <p>/thermal/gpu: <span id="thermal-gpu">loading...</span></p>
        <p>/thermal/cpu: <span id="thermal-cpu">loading...</span></p>
        <p>/session/uptime: <span id="session-uptime">loading...</span></p>
        <p>/device/resource/disk: <span id="device-disk">loading...</span></p>
        <p>/device/resource/mem: <span id="device-mem">loading...</span></p>
        <p>/vnc/status: <span id="vnc-status">loading...</span></p>
        <p>/power/throttle: <span id="power-throttle">loading...</span></p>
    </div>

    <h2>Actions</h2>
    <p>
        <button onclick="postAction('/power/restart')">Restart</button>
        <button onclick="postAction('/power/poweroff')">Power off</button>
        <button onclick="postAction('/power/sleep')">Sleep</button>
        <button onclick="postAction('/power/hibernate')">Hibernate</button>
    </p>
    <p>
        <button onclick="postAction('/vnc/start')">Start VNC</button>
        <button onclick="postAction('/vnc/start/websocket')">Start VNC WebSocket</button>
        <button onclick="postAction('/vnc/start/novnc')">Start VNC noVNC</button>
        <button onclick="postAction('/vnc/stop')">Stop VNC</button>
        <button onclick="postAction('/vnc/stop/novnc')">Stop noVNC</button>
    </p>
    <p>
        <button onclick="getAction('/ironmouse/ap/enable')">Enable Ironmouse AP</button>
        <button onclick="getAction('/ironmouse/ap/disable')">Disable Ironmouse AP</button>
        <button onclick="getAction('/ironmouse/eth/enable')">Enable Ironmouse Ethernet sharing</button>
        <button onclick="getAction('/ironmouse/eth/disable')">Disable Ironmouse Ethernet sharing</button>
    </p>

    <h2>Set clock</h2>
    <form id="clock-form">
        <label for="datetime">Datetime:</label>
        <input id="datetime" name="datetime" type="datetime-local">
        <button type="submit">Set time</button>
    </form>

    <h2>Result</h2>
    <pre id="output">Click a button to load data.</pre>

    <script>
        const output = document.getElementById('output');
        const datetimeInput = document.getElementById('datetime');
        const statusFields = [
            ['device-model', '/device/model'],
            ['device-serial', '/device/serial'],
            ['device-revision', '/device/revision'],
            ['kernel-cmdline', '/kernel/cmdline'],
            ['kernel-version', '/kernel/version'],
            ['device-hostname', '/device/hostname'],
            ['thermal-gpu', '/thermal/gpu'],
            ['thermal-cpu', '/thermal/cpu'],
            ['session-uptime', '/session/uptime'],
            ['device-disk', '/device/resource/disk'],
            ['device-mem', '/device/resource/mem'],
            ['vnc-status', '/vnc/status'],
            ['power-throttle', '/power/throttle'],
        ];

        function localDateTimeValue() {
            const now = new Date();
            const localTime = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
            return localTime.toISOString().slice(0, 16);
        }

        function setOutput(value) {
            output.textContent = value;
        }

        async function loadField(elementId, path) {
            const element = document.getElementById(elementId);
            try {
                const response = await fetch(path);
                const body = await response.text();
                element.textContent = body;
            } catch (error) {
                element.textContent = String(error);
            }
        }

        async function loadText(path) {
            setOutput('Loading ' + path + '...');
            try {
                const response = await fetch(path);
                const body = await response.text();
                setOutput(body);
            } catch (error) {
                setOutput(String(error));
            }
        }

        async function postAction(path, body) {
            setOutput('Sending request to ' + path + '...');
            try {
                const response = await fetch(path, {
                    method: 'POST',
                    headers: body ? {'Content-Type': 'application/json'} : {},
                    body: body ? JSON.stringify(body) : undefined,
                });
                const text = await response.text();
                setOutput(text);
            } catch (error) {
                setOutput(String(error));
            }
        }

        async function getAction(path) {
            setOutput('Sending request to ' + path + '...');
            try {
                const response = await fetch(path);
                const text = await response.text();
                setOutput(text);
            } catch (error) {
                setOutput(String(error));
            }
        }

        document.getElementById('clock-form').addEventListener('submit', async (event) => {
            event.preventDefault();
            const value = datetimeInput.value || localDateTimeValue();
            const payload = { datetime: value.replace('T', ' ') + ':00' };
            await postAction('/settings/time', payload);
        });

        for (const [elementId, path] of statusFields) {
            loadField(elementId, path);
        }
    </script>
</body>
</html>
""",
    200,
    {"content-type": "text/html; charset=utf-8"},
    )

# This is a really shitty way to implement the theming system
# In the future this should be configurable
THEME = "butchervanity"
app.mount("/theme", Path(__file__).resolve().parent / "vanity" / "themes" / THEME)

@app.get("/")
def index(req):
    return _webui_html()

@app.get("/device/model")
def model(req):
    """Return the device model string.

    Request:
    - `GET /device/model`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing the hardware model string.
    """
    return backend.model()


@app.get("/device/serial")
def serial(req):
    """Return the device serial string.

    Request:
    - `GET /device/serial`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing the device serial number.
    """
    return backend.serial()


@app.get("/device/revision")
def revision(req):
    """Return the device revision string.

    Request:
    - `GET /device/revision`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing the board revision string.
    """
    return backend.revision()


@app.get("/kernel/cmdline")
def cmdline(req):
    """Return the kernel command line.

    Request:
    - `GET /kernel/cmdline`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing `/proc/cmdline`.
    """
    return backend.cmdline()


@app.get("/kernel/version")
def linux_ver(req):
    """Return the kernel version string.

    Request:
    - `GET /kernel/version`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing `/proc/version`.
    """
    return backend.linux_ver()


@app.get("/device/hostname")
def hostname(req):
    """Return the current system hostname.

    Request:
    - `GET /device/hostname`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing the hostname.
    """
    return backend.hostname()


@app.get("/thermal/gpu")
def gputemp(req):
    """Return the GPU temperature in Celsius.

    Request:
    - `GET /thermal/gpu`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing a floating-point temperature in Celsius.
    """
    return backend.gpu_temperature_c()


@app.get("/thermal/cpu")
def cputemp(req):
    """Return the CPU temperature in Celsius.

    Request:
    - `GET /thermal/cpu`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing a floating-point temperature in Celsius.
    """
    return backend.cpu_temperature_c()


@app.post("/settings/time")
def set_time(req):
    """Set the system clock.

    Request:
    - `POST /settings/time`
    - Accepts either JSON or query parameters.
    - Time value lookup order:
      - JSON field `datetime`
      - JSON field `time`
      - Query parameter `datetime`
      - Query parameter `time`
    - Expected time format:
      - `YYYY-MM-DD HH:MM:SS`

    Response:
    - `400 Bad Request` when no datetime value is supplied.
      - Body: `{"ok": false, "error": "Missing datetime value"}`
    - `500 Internal Server Error` when the backend command fails.
      - Body:
        - `{"ok": false, "command": [...], "stderr": "...", "stdout": "..."}`
    - `200 OK` on success.
      - Body:
        - `{"ok": true, "command": [...], "stdout": "...", "stderr": "..."}`
    """
    payload = req.json if isinstance(req.json, dict) else {}
    datetime_str = (
        payload.get("datetime")
        or payload.get("time")
        or req.query.get("datetime")
        or req.query.get("time")
    )

    if not datetime_str:
        return {"ok": False, "error": "Missing datetime value"}, 400

    result = backend.set_time(datetime_str)
    if not result.ok:
        return {
            "ok": False,
            "command": list(result.command),
            "stderr": result.stderr,
            "stdout": result.stdout,
        }, 500

    return {
        "ok": True,
        "command": list(result.command),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@app.get("/session/uptime")
def uptime(req):
    """Return the system uptime.

    Request:
    - `GET /session/uptime`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body containing the contents of `/proc/uptime`.
    """
    return backend.uptime()


@app.get("/device/resource/disk")
def disk(req):
    """Return disk resource information.

    Request:
    - `GET /device/resource/disk`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Plain text body representing the backend disk information structure.
    """
    return backend.disk_info()


@app.get("/device/resource/mem")
def mem(req):
    """Return memory usage information.

    Request:
    - `GET /device/resource/mem`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - JSON body:
        - `{"ok": true, "total_mib": <int>, "used_mib": <int>, "free_mib": <int>}`
    - `500 Internal Server Error` when memory usage cannot be read.
        - Body: `{"ok": false, "error": "Unable to read memory usage"}`
    """
    usage = backend.memory_usage()
    if usage is None:
        return {"ok": False, "error": "Unable to read memory usage"}, 500

    return {"ok": True, **asdict(usage)}


@app.post("/vnc/start")
def vnc_start(req):
    """Start the tracked WayVNC process.

    Request:
    - `POST /vnc/start`
    - No body is required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true, "wayvnc_pid": <int>}`
    - `500 Internal Server Error` if startup fails.
      - Body: `{"ok": false, "error": "..."}`
    """
    try:
        pid = backend.start_wayvnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "wayvnc_pid": pid}


@app.post("/vnc/start/websocket")
def vnc_start_websocket(req):
    """Start WayVNC in WebSocket mode.

    Request:
    - `POST /vnc/start/websocket`
    - No body is required.

    Response:
    - `200 OK`
    - JSON body:
        - `{"ok": true, "wayvnc_pid": <int>, "websocket": true}`
    - `500 Internal Server Error` if startup fails.
        - Body: `{"ok": false, "error": "..."}`
    """
    try:
        pid = backend.start_wayvnc_websocket()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "wayvnc_pid": pid, "websocket": True}


# Most likely you will want to start WayVNC + noVNC together from a web UI
# You likely do not need raw WayVNC or WayVNC over WebSockets on something like a school iPad
@app.post("/vnc/start/novnc")
def vnc_start_novnc(req):
    """Start WayVNC with the noVNC proxy stack.

    Request:
    - `POST /vnc/start/novnc`
    - No body is required.

    Response:
    - `200 OK`
    - JSON body is whatever the backend returns, typically including:
        - `ok`
        - `wayvnc_pid`
        - `novnc_pid`
    - `500 Internal Server Error` if startup fails.
        - Body: `{"ok": false, "error": "..."}`
    """
    try:
        result = backend.start_wayvnc_with_novnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, **result}


@app.post("/vnc/stop")
def vnc_stop(req):
    """Stop the tracked WayVNC process.

    Request:
    - `POST /vnc/stop`
    - No body is required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true, "stopped": <bool>}`
    - `500 Internal Server Error` if shutdown fails.
      - Body: `{"ok": false, "error": "..."}`
    """
    try:
        stopped = backend.stop_wayvnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "stopped": stopped}


@app.post("/vnc/stop/novnc")
def vnc_stop_novnc(req):
    """Stop the tracked noVNC process.

    Request:
    - `POST /vnc/stop/novnc`
    - No body is required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true, "stopped": <bool>}`
    - `500 Internal Server Error` if shutdown fails.
      - Body: `{"ok": false, "error": "..."}`
    """
    try:
        stopped = backend.stop_novnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "stopped": stopped}


@app.get("/vnc/status")
def vncstatus(req):
    """Return the current VNC status object.

    Request:
    - `GET /vnc/status`
    - No query parameters or body are required.

    Response:
    - Returns the backend status object directly.
    - The current route returns the callable itself instead of invoking it,
        so the response is the router's representation of that object rather than
        a structured status payload.
    """
    return backend.get_vnc_status


@app.get("/power/restart")
def restart(req):
    """Restart the system.

    Request:
    - `GET /power/restart`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true}`
    """
    backend.reboot()
    return {"ok": True}


@app.get("/power/poweroff")
def poweroff(req):
    """Power off the system.

    Request:
    - `GET /power/poweroff`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true}`
    """
    backend.poweroff()
    return {"ok": True}


@app.get("/power/sleep")
def sleep(req):
    """Suspend the system to sleep.

    Request:
    - `GET /power/sleep`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true}`
    """
    backend.sleep()
    return {"ok": True}


@app.get("/power/hibernate")
def hibernate(req):
    """Hibernate the system.

    Request:
    - `GET /power/hibernate`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - JSON body: `{"ok": true}`
    """
    backend.hibernate()
    return {"ok": True}


@app.get("/power/throttle")
def throttled(req):
    data = backend.throttled()
    return {"ok": True, "data": data}

@app.get("/ironmouse/ap/enable")
def ap_enable(req):
    backend.enable_ap()
    return {"ok": True}

@app.get("/ironmouse/eth/enable")
def eth_enable(req):
    backend.enable_eth_share()
    return {"ok": True}

@app.get("/ironmouse/ap/disable")
def ap_enable(req):
    backend.disable_ap()
    return {"ok": True}

@app.get("/ironmouse/eth/disable")
def eth_enable(req):
    backend.disable_eth_share()
    return {"ok": True}

@app.post("/files/upload")
def upload_file(req):
    """Upload raw bytes to a path.

    Request:
    - `POST /files/upload`
    - Target path is read from the first available value among:
        - JSON field `path`
        - JSON field `file`
        - JSON field `source`
        - JSON field `target`
        - JSON field `name`
        - Query parameter `path`
        - Query parameter `file`
        - Query parameter `source`
        - Query parameter `target`
        - Query parameter `name`
    - Body:
        - Raw bytes from the HTTP request body.

    Response:
    - `200 OK`
        - JSON body returned by the backend, typically including:
            - `ok`
            - `path`
            - `bytes_written`
            - `content_type`
    - `400 Bad Request`
        - `{"ok": false, "error": "Missing file path"}`
        - `{"ok": false, "error": "Target path is a directory"}`
    - `403 Forbidden`
        - `{"ok": false, "error": "..."}` for paths outside the allowed home-directory scope.
    - `413 Payload Too Large`
        - `{"ok": false, "error": "..."}` when the upload exceeds the backend limit.
    - `500 Internal Server Error`
        - `{"ok": false, "error": "..."}` for write failures.
    """
    target_path = _get_file_path(req)
    if not target_path:
        return {"ok": False, "error": "Missing file path"}, 400

    payload = req.body if req.body is not None else b""

    try:
        result = backend.upload_file(target_path, payload)
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}, 403
    except IsADirectoryError:
        return {"ok": False, "error": "Target path is a directory"}, 400
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 413
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {
        "ok": True,
        **result,
    }


@app.get("/files/download")
def download_file(req):
    """Download raw bytes from a path.

    Request:
    - `GET /files/download`
    - Source path is read from the first available value among:
        - JSON field `path`
        - JSON field `file`
        - JSON field `source`
        - JSON field `target`
        - JSON field `name`
        - Query parameter `path`
        - Query parameter `file`
        - Query parameter `source`
        - Query parameter `target`
        - Query parameter `name`

    Response:
    - `200 OK`
        - Binary response body containing the file bytes.
        - `Content-Type` is set from backend best-effort detection.
    - `400 Bad Request`
        - `{"ok": false, "error": "Missing file path"}`
        - `{"ok": false, "error": "Path is a directory"}`
    - `403 Forbidden`
        - `{"ok": false, "error": "..."}` for paths outside the allowed home-directory scope.
    - `404 Not Found`
        - `{"ok": false, "error": "File not found"}`
    - `413 Payload Too Large`
        - `{"ok": false, "error": "..."}` when the file exceeds the backend limit.
    - `500 Internal Server Error`
        - `{"ok": false, "error": "..."}` for read failures.
    """
    source_path = _get_file_path(req)
    if not source_path:
        return {"ok": False, "error": "Missing file path"}, 400

    try:
        result = backend.download_file(source_path)
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}, 403
    except FileNotFoundError:
        return {"ok": False, "error": "File not found"}, 404
    except IsADirectoryError:
        return {"ok": False, "error": "Path is a directory"}, 400
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 413
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500

    return BinaryResponse(result["data"], content_type=result["content_type"])


@app.get("/vnc/preview")
def screenshot(req):
    """Return a screenshot of the current VNC framebuffer.

    Request:
    - `GET /vnc/preview`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Binary response body containing screenshot bytes.
    - `Content-Type` is taken from the backend result.
    - `403 Forbidden` is returned when screenshot capture fails.
      - Body: `{"ok": false, "error": "..."}`
    """
    try:
        result = backend.screenshot()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 403

    return BinaryResponse(result["data"], content_type=result["content_type"])


@app.get("/test")
def test(req):
    """Return a plain-text test response.

    Request:
    - `GET /test`
    - No query parameters or body are required.

    Response:
    - `200 OK`
    - Body: `Hello`
    - Custom response header: `hello: content`
    """
    return TextResponse("Hello", 200, {"hello": "content"})

try:
    app.run()
except KeyboardInterrupt:
    sys.exit(0)
