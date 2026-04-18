#!/usr/bin/env python3

from dataclasses import asdict, is_dataclass
import json
import os
import platform
import psutil
import socket
import subprocess
import time
from datetime import datetime


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"<error reading {path}: {e}>"


def run_command(command):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError:
        return {
            "command": command,
            "error": f"command not found: {command[0]}",
        }
    except Exception as e:
        return {
            "command": command,
            "error": str(e),
        }


def to_jsonable(value):
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if hasattr(value, "_asdict"):
        return to_jsonable(value._asdict())
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def parse_key_value_lines(text):
    parsed = {}
    for raw_line in text.splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        parsed.setdefault(key, []).append(value)
    return parsed


def first_value(values, default=None):
    if not values:
        return default
    if isinstance(values, list):
        return values[0] if values else default
    return values


def get_system_info():
    return {
        "time": datetime.utcnow().isoformat() + "Z",
        "hostname": platform.node(),
        "platform": platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "uptime_seconds": time.time() - psutil.boot_time(),
    }


def get_cpu_info():
    cpu_freq = psutil.cpu_freq()
    return {
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "cpu_percent_total": psutil.cpu_percent(interval=1),
        "cpu_percent_per_core": psutil.cpu_percent(interval=1, percpu=True),
        "load_avg": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "cpu_times": psutil.cpu_times()._asdict(),
        "cpu_freq": cpu_freq._asdict() if cpu_freq else None,
    }


def get_memory_info():
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    return {
        "virtual_memory": vm._asdict(),
        "swap_memory": sm._asdict(),
    }


def get_hardware_info():
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def get_networkmanager_info():
    return {
        "version": run_command(["nmcli", "--version"]),
        "general_status": run_command(["nmcli", "-t", "--separator", "|", "general", "status"]),
        "device_status": run_command(["nmcli", "-t", "--separator", "|", "device", "status"]),
        "active_connections": run_command([
            "nmcli",
            "-t",
            "--separator",
            "|",
            "-f",
            "NAME,UUID,TYPE,DEVICE,AUTOCONNECT",
            "connection",
            "show",
            "--active",
        ]),
        "device_details": run_command(["nmcli", "-t", "--separator", "|", "device", "show"]),
        "wifi_access_points": run_command([
            "nmcli",
            "-t",
            "--separator",
            "|",
            "-f",
            "IN-USE,BSSID,SSID,MODE,CHAN,FREQ,RATE,SIGNAL,BARS,SECURITY",
            "device",
            "wifi",
            "list",
        ]),
        "connection_profiles": run_command([
            "nmcli",
            "-t",
            "--separator",
            "|",
            "-f",
            "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,PERMISSIONS",
            "connection",
            "show",
        ]),
        "dns_resolver": run_command(["nmcli", "general", "status"]),
    }


def get_disk_info():
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts,
                "usage": usage._asdict(),
            })
        except Exception:
            continue
    return disks


def get_network_info():
    return {
        "hostname": platform.node(),
        "interfaces": psutil.net_if_addrs(),
        "stats": psutil.net_if_stats(),
        "io_counters": psutil.net_io_counters(pernic=True),
        "route_table": read_file("/proc/net/route"),
        "arp_table": read_file("/proc/net/arp"),
        "resolv_conf": read_file("/etc/resolv.conf"),
        "hosts": read_file("/etc/hosts"),
        "connections": [c._asdict() for c in psutil.net_connections()],
        "networkmanager": get_networkmanager_info(),
    }


def get_processes():
    procs = []
    attrs = [
        "pid",
        "ppid",
        "name",
        "exe",
        "cwd",
        "cmdline",
        "username",
        "status",
        "cpu_percent",
        "memory_percent",
        "create_time",
        "nice",
        "num_threads",
        "terminal",
    ]
    for p in psutil.process_iter(attrs=attrs):
        try:
            info = dict(p.info)
            info["cpu_times"] = p.cpu_times()._asdict()
            info["memory_info"] = p.memory_info()._asdict()
            info["memory_full_info"] = p.memory_full_info()._asdict() if hasattr(p, "memory_full_info") else None
            info["io_counters"] = p.io_counters()._asdict() if hasattr(p, "io_counters") else None
            info["open_files"] = [f._asdict() for f in p.open_files()]
            info["connections"] = [c._asdict() for c in p.connections()]
            info["threads"] = [t._asdict() for t in p.threads()]
            info["children"] = [child.pid for child in p.children(recursive=False)]
            info["num_fds"] = p.num_fds() if hasattr(p, "num_fds") else None
            info["num_ctx_switches"] = p.num_ctx_switches()._asdict()
            procs.append(info)
        except Exception:
            continue
    return procs


def get_procfs_kernel_state():
    return {
        "meminfo": read_file("/proc/meminfo"),
        "cpuinfo": read_file("/proc/cpuinfo"),
        "stat": read_file("/proc/stat"),
        "uptime": read_file("/proc/uptime"),
        "loadavg": read_file("/proc/loadavg"),
        "vmstat": read_file("/proc/vmstat"),
        "interrupts": read_file("/proc/interrupts"),
        "softirqs": read_file("/proc/softirqs"),
        "cmdline": read_file("/proc/cmdline"),
        "modules": read_file("/proc/modules"),
        "version": read_file("/proc/version"),
    }


def get_environment():
    return dict(os.environ)


def main():
    snapshot = {
        "system": get_system_info(),
        "hardware": get_hardware_info(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "disks": get_disk_info(),
        "network": get_network_info(),
        "processes": get_processes(),
        "kernel_procfs": get_procfs_kernel_state(),
        "environment": get_environment(),
    }

    output_file = f"system_snapshot_{int(time.time())}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, default=to_jsonable)

    print(f"Snapshot written to {output_file}")


if __name__ == "__main__":
    main()