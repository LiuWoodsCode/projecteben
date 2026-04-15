import sys
from dataclasses import asdict

from flaskish import App, BinaryResponse, TextResponse
import backend as backend
backend = backend.Api()
app = App()


def _get_file_path(req):
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

@app.get("/device/model")
def model(req):
    return backend.model()

@app.get("/device/serial")
def serial(req):
    return backend.serial()

@app.get("/device/revision")
def revision(req):
    return backend.revision()

@app.get("/kernel/cmdline")
def cmdline(req):
    return backend.cmdline()

@app.get("/kernel/version")
def linux_ver(req):
    return backend.linux_ver()

@app.get("/device/hostname")
def hostname(req):
    return backend.hostname()

@app.get("/thermal/gpu")
def gputemp(req):
    return backend.gpu_temperature_c()

@app.get("/thermal/cpu")
def cputemp(req):
    return backend.cpu_temperature_c()

@app.post("/settings/time")
def set_time(req):
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
    return backend.uptime()

@app.get("/device/resource/disk")
def disk(req):
    return backend.disk_info()

@app.get("/device/resource/mem")
def mem(req):
    usage = backend.memory_usage()
    if usage is None:
        return {"ok": False, "error": "Unable to read memory usage"}, 500

    return {"ok": True, **asdict(usage)}

@app.post("/vnc/start")
def vnc_start(req):
    try:
        pid = backend.start_wayvnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "wayvnc_pid": pid}

@app.post("/vnc/start/websocket")
def vnc_start_websocket(req):
    try:
        pid = backend.start_wayvnc_websocket()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "wayvnc_pid": pid, "websocket": True}

@app.post("/vnc/start/novnc")
def vnc_start_novnc(req):
    try:
        result = backend.start_wayvnc_with_novnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, **result}

@app.post("/vnc/stop")
def vnc_stop(req):
    try:
        stopped = backend.stop_wayvnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "stopped": stopped}

@app.post("/vnc/stop/novnc")
def vnc_stop_novnc(req):
    try:
        stopped = backend.stop_novnc()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "stopped": stopped}

@app.get("/vnc/status")
def vncstatus(req):
    return backend.get_vnc_status

@app.get("/power/restart")
def restart(req):
    backend.reboot()
    return {"ok": True}

@app.get("/power/poweroff")
def poweroff(req):
    backend.poweroff()
    return {"ok": True}

@app.get("/power/sleep")
def sleep(req): 
    backend.sleep()
    return {"ok": True}

@app.get("/power/hibernate")
def hibernate(req):     
    backend.hibernate()
    return {"ok": True}


@app.post("/files/upload")
def upload_file(req):
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

@app.get("/test")
def test(req):
    return TextResponse("Hello", 200, {"hello": "content"})

try:
    app.run()
except KeyboardInterrupt:
    sys.exit(0)
