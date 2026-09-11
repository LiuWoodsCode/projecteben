# Ranboo command-line client

`ranboo.py` is a standalone, platform-neutral client for the Ranboo HTTP API.
It is intentionally not a Python package. It requires Python 3.10 or newer and
the `requests` library already installed in your Python environment.

Run it directly:

```console
python3 ranboo.py reboot --ip 10.42.0.1
python3 ranboo.py device --ip 10.42.0.1
python3 ranboo.py fan governor disable --ip 10.42.0.1
python3 ranboo.py fan state 4 --ip 10.42.0.1
```

Connection options may be placed before the command or at the end of the
command. The defaults are `--ip 10.42.0.1`, `--port 8000`, and `--timeout 8`.

## Commands

```text
list                         Check only /test on all three advertised IPs
test                         Verify that /test has the Ranboo signature
web-ui                       Print the web UI HTML
theme ASSET -o FILE          Download an active-theme asset

device                       Show a combined device summary
model                        Show the hardware model
serial                       Show the serial number
revision                     Show the board revision
hostname                     Show the hostname
cmdline                      Show the kernel command line
kernel-version               Show the kernel version
uptime                       Show raw uptime
disk                         Show disk usage
memory                       Show memory usage (alias: mem)
temperature [all|cpu|gpu|pmic]

fan show
fan control
fan governor [show|enable|disable]
fan state [VALUE]            Omit VALUE to read the current state

set-time DATETIME
reboot
poweroff
sleep
hibernate
throttle

ap enable|disable
ethernet enable|disable      Alias: eth

vnc status
vnc start [--mode direct|websocket|novnc]
vnc stop [--target wayvnc|novnc|all]

upload LOCAL REMOTE
download REMOTE LOCAL
```

JSON API responses are pretty-printed. HTTP and connection failures are sent
to standard error and return exit status 1; invalid command syntax returns the
usual `argparse` exit status 2. Downloads stream directly to disk.

The Hyprland restart and VNC preview endpoints are deliberately not exposed.
