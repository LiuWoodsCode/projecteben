# Ranboo HTTP API

This document describes every HTTP endpoint registered by `main.py` in the
current Ranboo server implementation.

## Server conventions

- Base URL: `http://<device-address>:8000` (`main.py` listens on
  `0.0.0.0:8000` by default).
- The API has no authentication or authorization layer.
- JSON responses use `Content-Type: application/json`. Text responses use
  `text/plain`; binary and static responses specify their own type where known.
- There is no CORS handling.
- For `GET`, `POST`, `PUT`, and `DELETE`, an unknown path or unregistered
  method/path combination returns `404` with the plain-text body `Not Found`
  (there is no distinct `405` response). Other methods, such as `PATCH`, are not
  implemented by the HTTP handler and receive its default `501` response.
- Unhandled handler/backend exceptions return `500` with a plain-text Python
  traceback. Endpoints with documented JSON error responses catch those errors
  explicitly.
- When a routed `POST` or `PUT` request is handled, its body is read into
  `req.body`. If the complete body is valid JSON, its decoded value is also
  exposed as `req.json`. The request parser has the same logic for `PATCH`, but
  the HTTP handler does not currently accept that method.
- Request bodies larger than 512 MiB are rejected before routing with `413` and
  the plain-text body `Request body too large`.
- The server does not currently register any `PUT`, `PATCH`, or `DELETE`
  endpoints.

## Endpoint index

| Method | Path | Response | Purpose |
| --- | --- | --- | --- |
| `GET` | `/device/model` | Text | Hardware model |
| `GET` | `/device/serial` | Text | Device serial number |
| `GET` | `/device/revision` | Text | Board revision |
| `GET` | `/device/hostname` | Text | System hostname |
| `GET` | `/device/resource/disk` | JSON | Mounted-volume usage |
| `GET` | `/device/resource/mem` | JSON | Memory usage |
| `GET` | `/kernel/cmdline` | Text | Kernel command line |
| `GET` | `/kernel/version` | Text | Kernel version |
| `GET` | `/session/uptime` | Text | Raw system uptime |
| `GET` | `/thermal/cpu` | Text | CPU temperature |
| `GET` | `/thermal/gpu` | Text | GPU temperature |
| `GET` | `/thermal/pmic` | Text | PMIC temperature |
| `GET` | `/thermal/fan` | JSON | Combined fan information |
| `GET` | `/thermal/fan/control` | JSON | Current fan controller |
| `GET` | `/thermal/fan/governor` | JSON | Thermal governor state |
| `POST` | `/thermal/fan/governor/enable` | JSON | Enable kernel fan control |
| `POST` | `/thermal/fan/governor/disable` | JSON | Enable userspace fan control |
| `GET` | `/thermal/fan/state` | JSON | Current and maximum fan states |
| `POST` | `/thermal/fan/state` | JSON | Set the userspace fan state |
| `POST` | `/settings/time` | JSON | Set the system clock |
| `GET` | `/power/restart` | JSON | Reboot the system |
| `GET` | `/power/poweroff` | JSON | Power off the system |
| `GET` | `/power/sleep` | JSON | Ask systemd to sleep |
| `GET` | `/power/hibernate` | JSON | Ask systemd to hibernate |
| `GET` | `/power/throttle` | JSON | Raspberry Pi throttle flags |
| `GET` | `/ironmouse/ap/enable` | JSON | Enable the Wi-Fi access point |
| `GET` | `/ironmouse/ap/disable` | JSON | Disable the Wi-Fi access point |
| `GET` | `/ironmouse/eth/enable` | JSON | Enable Ethernet sharing |
| `GET` | `/ironmouse/eth/disable` | JSON | Disable Ethernet sharing |
| `POST` | `/vnc/start` | JSON | Start WayVNC |
| `POST` | `/vnc/start/websocket` | JSON | Start WayVNC in WebSocket mode |
| `POST` | `/vnc/start/novnc` | JSON | Start WayVNC and noVNC |
| `POST` | `/vnc/stop` | JSON | Stop WayVNC |
| `POST` | `/vnc/stop/novnc` | JSON | Stop noVNC |
| `GET` | `/vnc/status` | JSON | Inspect tracked VNC processes |
| `GET` | `/vnc/preview` | PNG | Capture the current display |
| `POST` | `/hyprland/restart` | JSON | Restart the Hyprland compositor |
| `POST` | `/files/upload` | JSON | Upload a file |
| `GET` | `/files/download` | Binary | Download a file |
| `GET` | `/test` | Text | Test the HTTP server |

## Device, kernel, and session information

The following endpoints accept no parameters:

| Endpoint | `200` body | Fallback behavior |
| --- | --- | --- |
| `GET /device/model` | Contents of `/proc/device-tree/model` | `unknown` if unreadable |
| `GET /device/serial` | `Serial` value from `/proc/cpuinfo` | `unknown` if absent/unreadable |
| `GET /device/revision` | `Revision` value from `/proc/cpuinfo` | `unknown` if absent/unreadable |
| `GET /device/hostname` | Current platform hostname | Empty text is possible |
| `GET /kernel/cmdline` | Contents of `/proc/cmdline` | `unknown` if unreadable |
| `GET /kernel/version` | Contents of `/proc/version` | `unknown` if unreadable |
| `GET /session/uptime` | Raw contents of `/proc/uptime` | See note below |

The uptime response normally contains two space-separated values in seconds:
system uptime and aggregate idle time. If `/proc/uptime` cannot be read, the
backend returns `null`, which the router cannot serialize; the present result
is therefore a plain-text `500` traceback rather than a JSON `null` response.

### `GET /device/resource/disk`

Returns a JSON array. Unreadable partitions are silently omitted.

```json
[
  {
    "device": "/dev/mmcblk0p2",
    "mountpoint": "/",
    "fstype": "ext4",
    "opts": "rw,relatime",
    "usage": {
      "total": 123456789,
      "used": 23456789,
      "free": 100000000,
      "percent": 19.0
    }
  }
]
```

All values inside `usage` except `percent` are bytes.

### `GET /device/resource/mem`

Returns memory values, in mebibytes, read from `free -m`:

```json
{
  "ok": true,
  "total_mib": 8192,
  "used_mib": 3072,
  "free_mib": 5120
}
```

If memory usage cannot be read or parsed, returns `500`:

```json
{"ok": false, "error": "Unable to read memory usage"}
```

## Temperatures and fan control

### Temperature endpoints

`GET /thermal/cpu`, `GET /thermal/gpu`, and `GET /thermal/pmic` return a
plain-text numeric temperature in degrees Celsius. CPU temperature is read from
`/sys/class/thermal/thermal_zone0/temp`; GPU and PMIC temperatures are read
with `vcgencmd`.

If a temperature is unavailable, the backend returns `null`. Because `null` is
not a supported top-level response type in the current router, this produces a
plain-text `500` traceback.

### `GET /thermal/fan`

Returns combined fan state:

```json
{
  "ok": true,
  "control": "governor",
  "governor": "enabled",
  "state": 2,
  "max_state": 4
}
```

`control` is `governor`, `userspace`, or `null`; `governor` is `enabled`,
`disabled`, or `null`; and either state may be `null` if its sysfs value cannot
be read. Returns `404` with `{"ok": false, "error": "..."}` if no fan cooling
device exists, or the same shape with `500` for another OS error.

### `GET /thermal/fan/control`

```json
{"ok": true, "control": "userspace"}
```

`control` is `governor`, `userspace`, or `null`. When it is `null`, `ok` is
`false` but the HTTP status remains `200`. An OS error returns a JSON error with
status `500`.

### `GET /thermal/fan/governor`

```json
{"ok": true, "governor": "enabled"}
```

`governor` is `enabled`, `disabled`, or `null`. When it is `null`, `ok` is
`false` but the HTTP status remains `200`. An OS error returns a JSON error with
status `500`.

### `POST /thermal/fan/governor/enable`

Enables the kernel thermal governor. No body is required. On success:

```json
{"ok": true, "governor": "enabled", "control": "governor"}
```

### `POST /thermal/fan/governor/disable`

Disables the kernel thermal governor, transferring fan control to userspace.
No body is required. On success:

```json
{"ok": true, "governor": "disabled", "control": "userspace"}
```

Both governor mutation endpoints return a JSON error with status `404` when the
thermal interface is missing, `403` on a permission failure, or `500` for an OS
error or when the requested state does not take effect.

### `GET /thermal/fan/state`

```json
{
  "ok": true,
  "state": 2,
  "max_state": 4,
  "control": "userspace"
}
```

Returns a JSON error with status `404` when no fan device exists and `500` for
another OS error. State values can be `null` when individual sysfs reads fail.

### `POST /thermal/fan/state`

Sets the fan cooling state. The value is taken first from the JSON body, then
from the `state` query parameter:

```json
{"state": 4}
```

The value must be an integer between zero and the device's `max_state`, and the
governor must already be disabled (`control` must be `userspace`). On success,
the response has the same shape as `GET /thermal/fan/state`.

Errors:

- `400` for a missing, non-integer, or out-of-range state.
- `409` when the thermal governor still controls the fan; the response includes
  the current `control` value.
- `404` when no fan cooling device exists.
- `403` on a permission failure.
- `500` for another OS error, or when the requested state does not take effect.
  The latter response includes `requested_state` and `actual_state`.

All error bodies are JSON with `ok: false` and an `error` string.

## Time configuration

### `POST /settings/time`

Sets the system clock using `sudo timedatectl set-time`. The value is selected
in this order:

1. JSON field `datetime`
2. JSON field `time`
3. Query parameter `datetime`
4. Query parameter `time`

The intended format is `YYYY-MM-DD HH:MM:SS`. The server does not validate it
itself; `timedatectl` determines whether it is acceptable.

Success (`200`):

```json
{
  "ok": true,
  "command": ["sudo", "timedatectl", "set-time", "2026-04-09 14:30:00"],
  "stdout": "",
  "stderr": ""
}
```

Missing value (`400`):

```json
{"ok": false, "error": "Missing datetime value"}
```

A nonzero `timedatectl` exit returns `500` with `ok: false` and the same
`command`, `stdout`, and `stderr` fields as the success response.

## Power and throttling

### Power actions

The power endpoints accept no parameters and return `{"ok": true}` after
launching the corresponding command:

| Endpoint | Command |
| --- | --- |
| `GET /power/restart` | `sudo reboot` |
| `GET /power/poweroff` | `sudo shutdown -h now` |
| `GET /power/sleep` | `sudo systemctl sleep` |
| `GET /power/hibernate` | `sudo systemctl hibernate` |

The handler does not inspect the command's exit code, so a command that runs but
returns nonzero still produces `200` and `{"ok": true}`. An exception while
launching it produces the server's plain-text `500` traceback.

`main.py` registers `/power/restart` twice. The custom router stores one handler
per method/path, so the later registration wins: the live endpoint calls
`backend.reboot()`. The earlier logout implementation is unreachable.

### `GET /power/throttle`

Returns the current and historical Raspberry Pi firmware throttling flags:

```json
{
  "ok": true,
  "data": {
    "raw": 0,
    "raw_hex": "0x0",
    "undervoltage_detected": false,
    "arm_frequency_capped": false,
    "currently_throttled": false,
    "soft_temperature_limit_active": false,
    "undervoltage_has_occurred": false,
    "arm_frequency_capping_has_occurred": false,
    "throttling_has_occurred": false,
    "soft_temperature_limit_has_occurred": false
  }
}
```

The data comes from `vcgencmd get_throttled`. Command or parse failures are not
caught and therefore produce a plain-text `500` traceback.

## Ironmouse network control

These endpoints accept no parameters:

| Endpoint | Command |
| --- | --- |
| `GET /ironmouse/ap/enable` | `sudo rpi-hotspot start-ap` |
| `GET /ironmouse/ap/disable` | `sudo rpi-hotspot stop-ap` |
| `GET /ironmouse/eth/enable` | `sudo rpi-hotspot enable-eth-share` |
| `GET /ironmouse/eth/disable` | `sudo rpi-hotspot disable-eth-share` |

Each returns `200` and `{"ok": true}` without checking the command exit code.
An exception while launching a command produces the server's plain-text `500`
traceback.

## VNC and compositor control

### Starting and stopping VNC

All VNC mutation endpoints accept no parameters.

`POST /vnc/start` starts the tracked WayVNC process:

```json
{"ok": true, "wayvnc_pid": 1234}
```

`POST /vnc/start/websocket` starts WayVNC with WebSocket support:

```json
{"ok": true, "wayvnc_pid": 1234, "websocket": true}
```

`POST /vnc/start/novnc` starts WayVNC, waits two seconds, and starts the noVNC
proxy from `$HOME/noVNC`:

```json
{"ok": true, "wayvnc_pid": 1234, "novnc_pid": 4321}
```

If the corresponding tracked process is already running, start calls reuse it.
Each start endpoint returns `500` and `{"ok": false, "error": "..."}` if
startup fails.

`POST /vnc/stop` stops tracked WayVNC, and `POST /vnc/stop/novnc` stops tracked
noVNC. Each returns:

```json
{"ok": true, "stopped": true}
```

`stopped` is `false` if that process has never been tracked by this server
instance. Stop failures return a JSON error with status `500`.

### `GET /vnc/status`

Returns the status of the two processes tracked by this server instance:

```json
{
  "wayvnc": {
    "running": true,
    "pid": 1234,
    "exit_code": null,
    "signal": null
  },
  "novnc": {
    "running": false,
    "pid": null,
    "exit_code": 0,
    "signal": null
  }
}
```

For a running process, `pid` is populated and both completion fields are
`null`. For a process that exited normally, `exit_code` is populated. For one
terminated by a signal, `signal` is its positive signal number. An untracked
process has `running: false` and all other fields set to `null`.

If tracked WayVNC has exited while tracked noVNC remains alive, this request
also stops noVNC before returning its status.

### `GET /vnc/preview`

Runs `grim -` and returns the captured bytes as `image/png`. Capture failures
return `403` with `{"ok": false, "error": "..."}`.

### `POST /hyprland/restart`

Kills the current user's Hyprland process, waits five seconds for the desktop
session to supervise a replacement, then selects the replacement's new instance
signature.

Success (`200`):

```json
{
  "ok": true,
  "old_pids": [1234],
  "new_pid": 5678,
  "old_signatures": ["old-signature"],
  "instance_signature": "new-signature"
}
```

Returns `501` with `{"ok": false, "error": "..."}` when Hyprland is not the
active compositor. A kill failure, missing supervised replacement, or missing
new instance signature returns the same JSON error shape with status `500`.

## File transfer

File paths are confined to the server user's home directory. Relative paths are
resolved beneath that directory; absolute paths must also remain inside it.
The maximum file size is 512 MiB.

Recognized path keys, in lookup order, are `path`, `file`, `source`, `target`,
and `name`. For each request the JSON body is checked before query parameters.
In practice, downloads are `GET` requests and the router does not parse bodies
for `GET`, so download paths must be supplied as query parameters.

### `POST /files/upload`

Writes the raw HTTP request body to the selected path and creates missing parent
directories. Supplying the path in the query string is recommended, because a
JSON body used to select the path is also written verbatim as the file content.

```bash
curl -X POST "http://127.0.0.1:8000/files/upload?path=uploads/test.txt" \
  --data-binary @./test.txt
```

Success (`200`):

```json
{
  "ok": true,
  "path": "/home/user/uploads/test.txt",
  "bytes_written": 123,
  "content_type": "text/plain"
}
```

Errors:

- `400`: missing path or target path is a directory.
- `403`: resolved path escapes the home directory.
- `413`: body/file exceeds 512 MiB. A request rejected by the router has the
  plain-text body described under server conventions; a backend size rejection
  uses a JSON error.
- `500`: another write error.

Handler-generated errors have the shape `{"ok": false, "error": "..."}`.

### `GET /files/download`

Returns the file bytes with a best-effort `Content-Type` inferred from the
filename, falling back to `application/octet-stream`.

```bash
curl -o test.txt \
  "http://127.0.0.1:8000/files/download?path=uploads/test.txt"
```

Errors, all as `{"ok": false, "error": "..."}`:

- `400`: missing path or path is a directory.
- `403`: resolved path escapes the home directory.
- `404`: file does not exist.
- `413`: file exceeds 512 MiB.
- `500`: another read error.

## Test endpoint

### `GET /test`

Returns `Hello` as plain text with status `200` and the custom response header
`hello: content`.

## Additional examples

```bash
# Read the hostname
curl http://127.0.0.1:8000/device/hostname

# Set the clock
curl -X POST http://127.0.0.1:8000/settings/time \
  -H 'Content-Type: application/json' \
  -d '{"datetime":"2026-04-09 14:30:00"}'

# Transfer fan control to userspace, then select state 4
curl -X POST http://127.0.0.1:8000/thermal/fan/governor/disable
curl -X POST http://127.0.0.1:8000/thermal/fan/state \
  -H 'Content-Type: application/json' \
  -d '{"state":4}'

# Start and inspect the noVNC stack
curl -X POST http://127.0.0.1:8000/vnc/start/novnc
curl http://127.0.0.1:8000/vnc/status
```
