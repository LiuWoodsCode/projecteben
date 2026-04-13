# Eben Desktop API

This document describes the HTTP API exposed by `server/main.py` and the custom router in `server/flaskish.py`.

## Base URL

The server listens on `http://0.0.0.0:8000`.

## Response Rules

The custom framework normalizes handler return values as follows:

- `dict` -> JSON response
- `str` -> plain text
- `bytes` -> binary response
- `int`, `float`, `list` -> plain text representation

Known issues:
- `/device/resource/disk` returns a Python-style stringified list, not JSON.

## Endpoints

### `GET /device/model`

Returns the device model as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: `unknown`

### `GET /device/serial`

Returns the device serial number as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: `unknown`

### `GET /device/revision`

Returns the device revision as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: `stub`

### `GET /device/hostname`

Returns the system hostname as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: `stickerbomb`

### `GET /kernel/cmdline`

Returns the kernel command line as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: the contents of `/proc/cmdline`

### `GET /kernel/version`

Returns the kernel version string as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: the contents of `/proc/version`

### `GET /session/uptime`

Returns the raw contents of `/proc/uptime` as plain text.

Live response on the current host:

- Status: `200 OK`
- Body: a two-value uptime string, for example `229018.75 448583.34`

### `GET /device/resource/disk`

Returns disk usage information for mounted partitions.

Response shape in the current implementation:
Important live behavior
- Status: `200 OK`

Each mount dictionary contains:

- `device`
- `mountpoint`
- `fstype`
- `opts`
- `usage`

The `usage` value is the `psutil.disk_usage()` structure as a dictionary.

### `GET /device/resource/mem`

Returns memory usage information.

Response shape in the current implementation:

- Status: `200 OK`
- Body: JSON with `ok`, `total_mib`, `used_mib`, and `free_mib`

### `POST /vnc/start`

Starts the existing `wayvnc` process in normal mode.

Response shape:

- Status: `200 OK`
- Body: JSON with `ok` and `wayvnc_pid`

### `POST /vnc/start/websocket`

Starts `wayvnc` with websocket support enabled.

Response shape:

- Status: `200 OK`
- Body: JSON with `ok`, `wayvnc_pid`, and `websocket: true`

### `POST /vnc/start/novnc`

Starts `wayvnc` and the noVNC proxy.

Response shape:

- Status: `200 OK`
- Body: JSON with `ok`, `wayvnc_pid`, and `novnc_pid`

### `POST /vnc/stop`

Stops the tracked `wayvnc` process.

Response shape:

- Status: `200 OK`
- Body: JSON with `ok` and `stopped`

### `POST /vnc/stop/novnc`

Stops the tracked noVNC proxy.

Response shape:

- Status: `200 OK`
- Body: JSON with `ok` and `stopped`

### `POST /settings/time`

Sets the system time.

Accepted inputs:

- JSON body field `datetime`
- JSON body field `time`
- Query parameter `datetime`
- Query parameter `time`

Expected format:

- `YYYY-MM-DD HH:MM:SS`

Validation behavior:

- If no value is supplied, the API returns `400 Bad Request` with JSON: `{"ok": false, "error": "Missing datetime value"}`
- If the underlying `timedatectl` command fails, the API returns `500 Internal Server Error` with JSON containing `ok`, `command`, `stdout`, and `stderr`
- On success, the API returns JSON containing `ok`, `command`, `stdout`, and `stderr`

### `POST /files/upload`

Uploads file bytes to a path under the current user home directory.

Accepted path inputs:

- Query parameter `path`
- JSON body field `path`
- JSON body field `file`
- JSON body field `target`
- JSON body field `name`

Request body:

- Raw file bytes in the HTTP body

Validation behavior:

- The resolved path must stay inside the home directory
- Files larger than 512 MB are rejected with `413 Payload Too Large`
- If the target path is a directory, the API returns `400 Bad Request`

Success response:

- Status: `200 OK`
- Body: JSON with `ok`, `path`, `bytes_written`, and `content_type`

### `GET /files/download`

Downloads a file from a path under the current user home directory.

Accepted path inputs:

- Query parameter `path`
- Query parameter `file`
- Query parameter `source`
- Query parameter `target`
- Query parameter `name`
- JSON body field `path`
- JSON body field `file`
- JSON body field `source`
- JSON body field `target`
- JSON body field `name`

Validation behavior:

- The resolved path must stay inside the home directory
- Files larger than 512 MB are rejected with `413 Payload Too Large`
- Missing files return `404 Not Found`
- Directories return `400 Bad Request`

Success response:

- Status: `200 OK`
- Body: raw file bytes with a best-effort `Content-Type`

### `GET /power/restart`

Triggers a system reboot and returns JSON:

```json
{"ok": true}
```

### `GET /power/poweroff`

Triggers system power off and returns JSON:

```json
{"ok": true}
```

### `GET /power/sleep`

Triggers system sleep and returns JSON:

```json
{"ok": true}
```

### `GET /power/hibernate`

Triggers system hibernate and returns JSON:

```json
{"ok": true}
```

## Example Requests

Fetch system hostname:

```bash
curl http://127.0.0.1:8000/device/hostname
```

Set the time with JSON:

```bash
curl -X POST http://127.0.0.1:8000/settings/time \
  -H 'Content-Type: application/json' \
  -d '{"datetime":"2026-04-09 14:30:00"}'
```

Start noVNC with wayvnc:

```bash
curl -X POST http://127.0.0.1:8000/vnc/start/novnc
```

Stop wayvnc:

```bash
curl -X POST http://127.0.0.1:8000/vnc/stop
```

Upload a text file:

```bash
curl -X POST "http://127.0.0.1:8000/files/upload?path=uploads/test.txt" \
  --data-binary @./test.txt
```

Download a file:

```bash
curl -o test.txt "http://127.0.0.1:8000/files/download?path=uploads/test.txt"
```
```