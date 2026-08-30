# Eben Desktop API

This document describes the HTTP API currently exposed by `main.py`, running on the custom router in `flaskish.py`.

## Base URL

- `http://0.0.0.0:8000`

## Framework Behavior

The router converts handler return values into HTTP responses using these rules:

- `dict` -> JSON response (`application/json`)
- `str` -> plain text (`text/plain`)
- `bytes` -> binary response (`application/octet-stream` unless explicitly overridden)
- `int`, `float`, `list` -> plain text string representation
- `(body, status)` tuple -> same conversion as above with explicit HTTP status

For `POST`, `PUT`, and `PATCH`, request body parsing behavior is:

- If body is valid JSON, it is available as `req.json`
- Raw bytes are always available as `req.body`
- Body size above `512 MB` is rejected before route handling with `413` and body `Request body too large`

## Endpoint Summary

### System identity

- `GET /device/model` -> plain text model string
- `GET /device/serial` -> plain text serial string
- `GET /device/revision` -> plain text revision string
- `GET /device/hostname` -> plain text hostname

### Kernel and session

- `GET /kernel/cmdline` -> plain text `/proc/cmdline`
- `GET /kernel/version` -> plain text `/proc/version`
- `GET /session/uptime` -> plain text uptime (contents of `/proc/uptime`)

### Thermal

- `GET /thermal/cpu` -> CPU temperature as plain text float (Celsius)
- `GET /thermal/gpu` -> GPU temperature as plain text float (Celsius)

### Resource usage

- `GET /device/resource/disk` -> JSON array of mounted volumes and their usage
- `GET /device/resource/mem` -> JSON memory summary

`/device/resource/mem` success shape:

```json
{
  "ok": true,
  "total_mib": 0,
  "used_mib": 0,
  "free_mib": 0
}
```

Possible failure:

- `500` with `{"ok": false, "error": "Unable to read memory usage"}`

### VNC control

- `POST /vnc/start` -> start `wayvnc`
- `POST /vnc/start/websocket` -> start `wayvnc -w`
- `POST /vnc/start/novnc` -> start `wayvnc` plus noVNC proxy
- `POST /vnc/stop` -> stop tracked `wayvnc`
- `POST /vnc/stop/novnc` -> stop tracked noVNC
- `GET /vnc/preview` -> PNG screenshot bytes
- `GET /vnc/status` -> return current wayvnc/noVNC process state, exit codes, and signals

Typical success responses:

```json
{"ok": true, "wayvnc_pid": 1234}
```

```json
{"ok": true, "wayvnc_pid": 1234, "websocket": true}
```

```json
{"ok": true, "wayvnc_pid": 1234, "novnc_pid": 4321}
```

```json
{"ok": true, "stopped": true}
```

### Time configuration

- `POST /settings/time`

Accepted time input locations:

- JSON field `datetime`
- JSON field `time`
- Query parameter `datetime`
- Query parameter `time`

Expected format:

- `YYYY-MM-DD HH:MM:SS`

Responses:

- `400` if missing value: `{"ok": false, "error": "Missing datetime value"}`
- `500` if `timedatectl` fails: `{"ok": false, "command": [...], "stderr": "...", "stdout": "..."}`
- `200` on success: `{"ok": true, "command": [...], "stdout": "...", "stderr": "..."}`

### File transfer

- `POST /files/upload` -> write bytes to a home-directory-bounded path
- `GET /files/download` -> read file bytes from a home-directory-bounded path

Accepted path keys (query first, then JSON if query absent):

- `path`
- `file`
- `source`
- `target`
- `name`

Upload request body:

- Raw bytes from HTTP body

Upload responses:

- `200`: `{"ok": true, "path": "...", "bytes_written": 123, "content_type": "..."}`
- `400`: missing path or path points to directory
- `403`: path escapes home directory
- `413`: payload over `512 MB`
- `500`: write failure

Download responses:

- `200`: raw file bytes with best-effort content type
- `400`: missing path or path is directory
- `403`: path escapes home directory
- `404`: file not found
- `413`: file over `512 MB`
- `500`: read failure

### Power control

- `GET /power/restart`
- `GET /power/poweroff`
- `GET /power/sleep`
- `GET /power/hibernate`

Each returns:

```json
{"ok": true}
```

### Test route

- `GET /test` -> plain text `Hello` with custom response header `hello: content`

## Example Requests

Hostname:

```bash
curl http://127.0.0.1:8000/device/hostname
```

Set time with JSON:

```bash
curl -X POST http://127.0.0.1:8000/settings/time \
  -H 'Content-Type: application/json' \
  -d '{"datetime":"2026-04-09 14:30:00"}'
```

Start noVNC stack:

```bash
curl -X POST http://127.0.0.1:8000/vnc/start/novnc
```

Stop wayvnc:

```bash
curl -X POST http://127.0.0.1:8000/vnc/stop
```

Upload file bytes:

```bash
curl -X POST "http://127.0.0.1:8000/files/upload?path=uploads/test.txt" \
  --data-binary @./test.txt
```

Download a file:

```bash
curl -o test.txt "http://127.0.0.1:8000/files/download?path=uploads/test.txt"
```
```
