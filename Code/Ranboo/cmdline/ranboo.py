#!/usr/bin/env python3
"""Platform-neutral command-line client for the Ranboo HTTP API."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import mimetypes
from pathlib import Path
import platform
import socket
import sys
from typing import Any, NoReturn
from urllib.parse import quote

import requests


DEFAULT_IP = "10.42.0.1"
DEFAULT_PORT = 8000
DEFAULT_TIMEOUT = 8.0
RANBOO_IPS = ("10.42.0.1", "10.42.1.1", "10.12.194.1")


class RanbooError(RuntimeError):
    """A request or local file operation that could not be completed."""


def positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def port_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return number


def fan_state_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


class RanbooClient:
    """Small HTTP wrapper that keeps CLI behavior consistent across commands."""

    def __init__(self, ip: str, port: int, timeout: float) -> None:
        host = ip.strip()
        if not host:
            raise RanbooError("the device IP or hostname cannot be empty")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": f"Ranboo-CLI/1 ({platform.system()}; {platform.machine()})",
            "X-Client-App": "Ranboo CLI",
            "X-Client-Platform": platform.system() or "unknown",
            "X-Client-Name": socket.gethostname() or "unknown",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        request_headers = dict(self.headers)
        if headers:
            request_headers.update(headers)
        try:
            response = requests.request(
                method,
                self.base_url + path,
                params=params,
                json=json_body,
                data=data,
                headers=request_headers,
                timeout=self.timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            raise RanbooError(f"unable to reach {self.base_url}: {exc}") from exc

        if not 200 <= response.status_code < 300:
            try:
                detail = response.text.strip()
            except requests.RequestException:
                detail = ""
            message = f"HTTP {response.status_code} from {method} {path}"
            if detail:
                message += f": {detail}"
            response.close()
            raise RanbooError(message)
        return response


def response_value(response: requests.Response) -> Any:
    content_type = response.headers.get("Content-Type", "").lower()
    if "application/json" in content_type:
        try:
            return response.json()
        except ValueError as exc:
            raise RanbooError("the server returned invalid JSON") from exc
    return response.text.strip()


def print_value(value: Any) -> None:
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(value)


def request_and_print(
    client: RanbooClient,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> None:
    response = client.request(method, path, params=params, json_body=json_body)
    try:
        print_value(response_value(response))
    finally:
        response.close()


def add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ip",
        metavar="ADDRESS",
        default=argparse.SUPPRESS,
        help=f"device IP address or hostname (default: {DEFAULT_IP})",
    )
    parser.add_argument(
        "--port",
        type=port_number,
        default=argparse.SUPPRESS,
        help=f"Ranboo HTTP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=argparse.SUPPRESS,
        metavar="SECONDS",
        help=f"request timeout (default: {DEFAULT_TIMEOUT:g})",
    )


def add_command(
    subparsers: argparse._SubParsersAction,
    name: str,
    help_text: str,
    *,
    aliases: tuple[str, ...] = (),
    connected: bool = True,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, aliases=list(aliases), help=help_text)
    if connected:
        add_connection_options(parser)
    return parser


def set_action(parser: argparse.ArgumentParser, action: str) -> None:
    parser.set_defaults(action=action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ranboo.py",
        description="Control and inspect a Ranboo device over its HTTP API.",
    )
    add_connection_options(parser)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    set_action(add_command(sub, "list", "check Ranboo on its three advertised IPs"), "list")
    set_action(add_command(sub, "test", "verify the Ranboo API signature"), "test")
    set_action(add_command(sub, "web-ui", "print the prototype web UI HTML"), "web_ui")

    theme = add_command(sub, "theme", "download an active-theme asset")
    theme.add_argument("asset", help="theme-relative path, such as style.css")
    theme.add_argument("--output", "-o", required=True, type=Path, help="local output file")
    set_action(theme, "theme")

    set_action(add_command(sub, "device", "show the device identity summary"), "device")
    for name, help_text in (
        ("model", "show the hardware model"),
        ("serial", "show the device serial number"),
        ("revision", "show the board revision"),
        ("hostname", "show the system hostname"),
        ("cmdline", "show the kernel command line"),
        ("kernel-version", "show the kernel version"),
        ("uptime", "show the raw system uptime"),
        ("disk", "show mounted-volume usage"),
    ):
        set_action(add_command(sub, name, help_text), name.replace("-", "_"))
    set_action(add_command(sub, "memory", "show memory usage", aliases=("mem",)), "memory")

    temperature = add_command(sub, "temperature", "show temperatures", aliases=("temp",))
    temperature.add_argument(
        "sensor", choices=("all", "cpu", "gpu", "pmic"), nargs="?", default="all"
    )
    set_action(temperature, "temperature")

    fan = add_command(sub, "fan", "inspect or control the cooling fan")
    fan_sub = fan.add_subparsers(dest="fan_command", metavar="COMMAND", required=True)
    fan_show = fan_sub.add_parser("show", help="show combined fan information")
    add_connection_options(fan_show)
    set_action(fan_show, "fan_show")
    fan_control = fan_sub.add_parser("control", help="show the current fan controller")
    add_connection_options(fan_control)
    set_action(fan_control, "fan_control")
    fan_governor = fan_sub.add_parser("governor", help="show or change the thermal governor")
    add_connection_options(fan_governor)
    fan_governor.add_argument("mode", choices=("show", "enable", "disable"), nargs="?", default="show")
    set_action(fan_governor, "fan_governor")
    fan_state = fan_sub.add_parser("state", help="show or set the userspace fan state")
    add_connection_options(fan_state)
    fan_state.add_argument("value", type=fan_state_number, nargs="?")
    set_action(fan_state, "fan_state")

    set_time = add_command(sub, "set-time", "set the device system clock")
    set_time.add_argument("datetime", help='time accepted by timedatectl, e.g. "2026-04-09 14:30:00"')
    set_action(set_time, "set_time")

    for name, help_text in (
        ("reboot", "reboot the device"),
        ("poweroff", "power off the device"),
        ("sleep", "put the device to sleep"),
        ("hibernate", "hibernate the device"),
        ("throttle", "show Raspberry Pi throttle flags"),
    ):
        set_action(add_command(sub, name, help_text), name)

    ap = add_command(sub, "ap", "enable or disable the Ironmouse Wi-Fi access point")
    ap.add_argument("mode", choices=("enable", "disable"))
    set_action(ap, "ap")
    ethernet = add_command(
        sub, "ethernet", "enable or disable Ironmouse Ethernet sharing", aliases=("eth",)
    )
    ethernet.add_argument("mode", choices=("enable", "disable"))
    set_action(ethernet, "ethernet")

    vnc = add_command(sub, "vnc", "start, stop, or inspect VNC")
    vnc_sub = vnc.add_subparsers(dest="vnc_command", metavar="COMMAND", required=True)
    vnc_status = vnc_sub.add_parser("status", help="show tracked VNC process status")
    add_connection_options(vnc_status)
    set_action(vnc_status, "vnc_status")
    vnc_start = vnc_sub.add_parser("start", help="start WayVNC")
    add_connection_options(vnc_start)
    vnc_start.add_argument(
        "--mode", choices=("direct", "websocket", "novnc"), default="direct"
    )
    set_action(vnc_start, "vnc_start")
    vnc_stop = vnc_sub.add_parser("stop", help="stop WayVNC, noVNC, or both")
    add_connection_options(vnc_stop)
    vnc_stop.add_argument(
        "--target", choices=("wayvnc", "novnc", "all"), default="wayvnc"
    )
    set_action(vnc_stop, "vnc_stop")

    upload = add_command(sub, "upload", "upload a local file to the device")
    upload.add_argument("local", type=Path, help="local source file")
    upload.add_argument("remote", help="destination path under the device user's home")
    set_action(upload, "upload")

    download = add_command(sub, "download", "download a file from the device")
    download.add_argument("remote", help="source path under the device user's home")
    download.add_argument("local", type=Path, help="local destination file")
    set_action(download, "download")

    return parser


SIMPLE_GET_PATHS = {
    "web_ui": "/",
    "model": "/device/model",
    "serial": "/device/serial",
    "revision": "/device/revision",
    "hostname": "/device/hostname",
    "cmdline": "/kernel/cmdline",
    "kernel_version": "/kernel/version",
    "uptime": "/session/uptime",
    "disk": "/device/resource/disk",
    "memory": "/device/resource/mem",
    "reboot": "/power/restart",
    "poweroff": "/power/poweroff",
    "sleep": "/power/sleep",
    "hibernate": "/power/hibernate",
    "throttle": "/power/throttle",
    "fan_show": "/thermal/fan",
    "fan_control": "/thermal/fan/control",
    "vnc_status": "/vnc/status",
}


def connection_values(args: argparse.Namespace) -> tuple[str, int, float]:
    return (
        getattr(args, "ip", DEFAULT_IP),
        getattr(args, "port", DEFAULT_PORT),
        getattr(args, "timeout", DEFAULT_TIMEOUT),
    )


def probe_address(ip: str, port: int, timeout: float) -> tuple[bool, str]:
    client = RanbooClient(ip, port, timeout)
    try:
        response = client.request("GET", "/test")
        try:
            body = response.text.strip()
            signature_ok = body == "Hello" and response.headers.get("hello") == "content"
        finally:
            response.close()
    except RanbooError as exc:
        return False, str(exc)
    if signature_ok:
        return True, "online"
    return False, "HTTP replied, but the Ranboo signature did not match"


def run_list(args: argparse.Namespace) -> int:
    _, port, timeout = connection_values(args)
    with ThreadPoolExecutor(max_workers=len(RANBOO_IPS)) as pool:
        results = list(pool.map(lambda ip: probe_address(ip, port, timeout), RANBOO_IPS))

    width = max(len("IP ADDRESS"), *(len(ip) for ip in RANBOO_IPS))
    print(f"{'IP ADDRESS':<{width}}  RANBOO")
    for ip, (online, detail) in zip(RANBOO_IPS, results):
        status = "yes" if online else f"no ({detail})"
        print(f"{ip:<{width}}  {status}")
    return 0


def run_test(client: RanbooClient) -> None:
    response = client.request("GET", "/test")
    try:
        body = response.text.strip()
        if body != "Hello" or response.headers.get("hello") != "content":
            raise RanbooError("the /test response did not match the Ranboo API signature")
    finally:
        response.close()
    print("Ranboo API: online")


def run_device(client: RanbooClient) -> None:
    fields = (
        ("model", "/device/model"),
        ("hostname", "/device/hostname"),
        ("serial", "/device/serial"),
        ("revision", "/device/revision"),
        ("kernel_version", "/kernel/version"),
        ("cmdline", "/kernel/cmdline"),
        ("uptime", "/session/uptime"),
    )
    values: dict[str, Any] = {}
    for name, path in fields:
        response = client.request("GET", path)
        try:
            values[name] = response_value(response)
        finally:
            response.close()
    print_value(values)


def run_temperature(client: RanbooClient, sensor: str) -> None:
    sensors = ("cpu", "gpu", "pmic") if sensor == "all" else (sensor,)
    values: dict[str, Any] = {}
    for name in sensors:
        response = client.request("GET", f"/thermal/{name}")
        try:
            values[name] = response_value(response)
        finally:
            response.close()
    if sensor == "all":
        print_value(values)
    else:
        print_value(values[sensor])


def run_vnc_stop(client: RanbooClient, target: str) -> None:
    paths = {
        "wayvnc": ("/vnc/stop",),
        "novnc": ("/vnc/stop/novnc",),
        "all": ("/vnc/stop/novnc", "/vnc/stop"),
    }[target]
    results: dict[str, Any] = {}
    for path in paths:
        response = client.request("POST", path)
        try:
            value = response_value(response)
        finally:
            response.close()
        if target == "all":
            results["novnc" if path.endswith("novnc") else "wayvnc"] = value
        else:
            print_value(value)
    if target == "all":
        print_value(results)


def write_response_file(response: requests.Response, destination: Path) -> int:
    if destination.exists() and destination.is_dir():
        raise RanbooError(f"local destination is a directory: {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        byte_count = 0
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    output.write(chunk)
                    byte_count += len(chunk)
    except OSError as exc:
        raise RanbooError(f"could not write {destination}: {exc}") from exc
    return byte_count


def run_upload(client: RanbooClient, local: Path, remote: str) -> None:
    if not local.is_file():
        raise RanbooError(f"local source is not a file: {local}")
    content_type = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
    try:
        with local.open("rb") as source:
            response = client.request(
                "POST",
                "/files/upload",
                params={"path": remote},
                data=source,
                headers={"Content-Type": content_type},
            )
    except OSError as exc:
        raise RanbooError(f"could not read {local}: {exc}") from exc
    try:
        print_value(response_value(response))
    finally:
        response.close()


def run_download(client: RanbooClient, remote: str, local: Path) -> None:
    response = client.request(
        "GET", "/files/download", params={"path": remote}, stream=True
    )
    try:
        byte_count = write_response_file(response, local)
    finally:
        response.close()
    print(f"Downloaded {byte_count} bytes to {local}")


def dispatch(args: argparse.Namespace) -> int:
    if args.action == "list":
        return run_list(args)

    ip, port, timeout = connection_values(args)
    client = RanbooClient(ip, port, timeout)

    if args.action in SIMPLE_GET_PATHS:
        request_and_print(client, "GET", SIMPLE_GET_PATHS[args.action])
    elif args.action == "test":
        run_test(client)
    elif args.action == "theme":
        asset = quote(args.asset.lstrip("/"), safe="/")
        response = client.request("GET", f"/theme/{asset}", stream=True)
        try:
            byte_count = write_response_file(response, args.output)
        finally:
            response.close()
        print(f"Downloaded {byte_count} bytes to {args.output}")
    elif args.action == "device":
        run_device(client)
    elif args.action == "temperature":
        run_temperature(client, args.sensor)
    elif args.action == "fan_governor":
        path = "/thermal/fan/governor"
        method = "GET" if args.mode == "show" else "POST"
        if args.mode != "show":
            path += f"/{args.mode}"
        request_and_print(client, method, path)
    elif args.action == "fan_state":
        if args.value is None:
            request_and_print(client, "GET", "/thermal/fan/state")
        else:
            request_and_print(
                client, "POST", "/thermal/fan/state", json_body={"state": args.value}
            )
    elif args.action == "set_time":
        request_and_print(
            client,
            "POST",
            "/settings/time",
            json_body={"datetime": args.datetime.replace("T", " ")},
        )
    elif args.action == "ap":
        request_and_print(client, "GET", f"/ironmouse/ap/{args.mode}")
    elif args.action == "ethernet":
        request_and_print(client, "GET", f"/ironmouse/eth/{args.mode}")
    elif args.action == "vnc_start":
        suffix = "" if args.mode == "direct" else f"/{args.mode}"
        request_and_print(client, "POST", f"/vnc/start{suffix}")
    elif args.action == "vnc_stop":
        run_vnc_stop(client, args.target)
    elif args.action == "upload":
        run_upload(client, args.local, args.remote)
    elif args.action == "download":
        run_download(client, args.remote, args.local)
    else:  # pragma: no cover - argparse and the command table keep this unreachable.
        raise RanbooError(f"unsupported command: {args.action}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except RanbooError as exc:
        print(f"ranboo.py: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ranboo.py: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
