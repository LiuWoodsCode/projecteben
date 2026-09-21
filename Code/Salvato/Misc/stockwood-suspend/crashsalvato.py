#!/usr/bin/env python3
"""
CrashSalvato - ProjectEben crash report generator.

Usage:

    import crashsalvato

    try:
        something_dangerous()
    except Exception:
        import traceback

        report = crashsalvato.generate_crash_log(traceback.format_exc())

        with open("/var/log/projecteben-crash.log", "w") as f:
            f.write(report)

generate_crash_log() only creates and returns the report. It does not
write anything to disk.
"""

from __future__ import annotations

import datetime
import os
import platform
import pwd
import shlex
import socket
import subprocess
from pathlib import Path


def _read_text(path: str) -> str:
    """Read a text file, returning a useful placeholder on failure."""
    try:
        return Path(path).read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()
    except Exception as exc:
        return f"[Unable to read {path}: {exc}]"


def _run_command(
    command: list[str],
    timeout: float = 10.0,
) -> str:
    """Run a command and return its combined stdout/stderr."""
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )

        output = result.stdout.strip()

        if output:
            return output

        if result.returncode != 0:
            return (
                f"[Command exited with status "
                f"{result.returncode}: "
                f"{shlex.join(command)}]"
            )

        return "[No output]"

    except FileNotFoundError:
        return f"[Command not found: {command[0]}]"

    except subprocess.TimeoutExpired:
        return (
            f"[Command timed out after {timeout:g} seconds: "
            f"{shlex.join(command)}]"
        )

    except Exception as exc:
        return f"[Unable to run {shlex.join(command)}: {exc}]"


def _tail(text: str, lines: int) -> str:
    """Return the last N lines of a string."""
    split = text.splitlines()

    if not split:
        return "[No output]"

    return "\n".join(split[-lines:])


def _cpuinfo_value(name: str) -> str | None:
    """Get a field from /proc/cpuinfo."""
    try:
        with open(
            "/proc/cpuinfo",
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:
            for line in f:
                key, separator, value = line.partition(":")

                if separator and key.strip().lower() == name.lower():
                    return value.strip()

    except OSError:
        pass

    return None


def _device_tree_value(filename: str) -> str | None:
    """Read a value from the Linux device tree."""
    path = Path("/proc/device-tree") / filename

    try:
        data = path.read_bytes()
        return data.rstrip(b"\x00").decode(
            "utf-8",
            errors="replace",
        )
    except OSError:
        return None


def _get_hardware_info() -> tuple[str, str, str]:
    """Return model, serial number, and Raspberry Pi revision."""
    model = (
        _device_tree_value("model")
        or _cpuinfo_value("model name")
        or platform.machine()
        or "Unknown"
    )

    serial = (
        _cpuinfo_value("Serial")
        or "Unknown"
    )

    revision = (
        _cpuinfo_value("Revision")
        or "Unknown"
    )

    return model, serial, revision


def _get_kernel_version() -> str:
    """
    Get the full kernel build string.

    /proc/version gives substantially more useful information for a
    crash report than simply calling uname -r.
    """
    version = _read_text("/proc/version")

    if version.startswith("[Unable to read"):
        return platform.platform()

    return version


def _get_os_release() -> str:
    """Get PRETTY_NAME from /etc/os-release."""
    try:
        with open(
            "/etc/os-release",
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    value = line.split("=", 1)[1].strip()

                    if (
                        len(value) >= 2
                        and value[0] == value[-1]
                        and value[0] in "\"'"
                    ):
                        value = value[1:-1]

                    return value

    except OSError as exc:
        return f"[Unable to read /etc/os-release: {exc}]"

    return "Unknown"


def _get_process_name(pid: int) -> str:
    """Get a process's short name."""
    try:
        return Path(
            f"/proc/{pid}/comm"
        ).read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()
    except OSError:
        return "Unknown"


def _get_process_command_line(pid: int) -> str:
    """Get a process's complete command line."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()

        args = [
            part.decode("utf-8", errors="replace")
            for part in raw.split(b"\x00")
            if part
        ]

        if not args:
            return "Unknown"

        return shlex.join(args)

    except OSError:
        return "Unknown"


def _get_boot_time() -> str:
    """Determine when the system was booted."""
    try:
        with open(
            "/proc/stat",
            "r",
            encoding="utf-8",
            errors="replace",
        ) as f:
            for line in f:
                if line.startswith("btime "):
                    timestamp = int(line.split()[1])

                    boot_time = datetime.datetime.fromtimestamp(
                        timestamp
                    )

                    return boot_time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )

    except (OSError, ValueError, IndexError):
        pass

    # Fallback to uptime.
    try:
        uptime = float(
            Path("/proc/uptime")
            .read_text(encoding="ascii")
            .split()[0]
        )

        boot_time = (
            datetime.datetime.now()
            - datetime.timedelta(seconds=uptime)
        )

        return boot_time.strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return "Unknown"


def _get_current_user() -> str:
    """Return the effective user's account name."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):
        return str(os.geteuid())


def _get_processes() -> str:
    """Get a snapshot of the process table."""
    # BSD-style "aux" gives us user, CPU, memory, PID and command
    # without depending on procps-specific formatting too heavily.
    return _run_command(["ps", "auxww"])


def _get_vclog(kind: str) -> str:
    """Retrieve Raspberry Pi VideoCore logs."""
    return _run_command(["vclog", f"--{kind}"])


def _get_dmesg(lines: int = 30) -> str:
    """Get the final N kernel log messages."""
    return _tail(
        _run_command(["dmesg", "--color=never"]),
        lines,
    )


def _get_journal(lines: int = 30) -> str:
    """Get the final N systemd journal entries."""
    return _run_command(
        [
            "journalctl",
            "--no-pager",
            "-n",
            str(lines),
        ]
    )


def generate_crash_log(
    additional_details: str = "",
    *,
    caller_pid: int | None = None,
) -> str:
    """
    Generate and return a complete CrashSalvato report.

    Parameters
    ----------
    additional_details:
        Arbitrary diagnostic information supplied by the application.
        A traceback is a particularly useful thing to put here.

    caller_pid:
        PID to describe in CALLER INFORMATION. By default this is the
        current process, which is normally the program importing this
        module.

    Returns
    -------
    str
        Complete crash report ready to be written to a file.
    """

    if caller_pid is None:
        caller_pid = os.getpid()

    now = datetime.datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    variant = "stockwood-suspend"
    model, serial, revision = _get_hardware_info()

    process_name = _get_process_name(caller_pid)
    command_line = _get_process_command_line(caller_pid)

    boot_time = _get_boot_time()
    current_user = _get_current_user()
    hostname = socket.gethostname()

    cmdline = _read_text("/proc/cmdline")
    processes = _get_processes()

    vc_message = _get_vclog("msg")
    vc_assert = _get_vclog("assert")

    dmesg = _get_dmesg(30)
    journal = _get_journal(30)

    if additional_details is None:
        additional_details = ""

    additional_details = str(additional_details).rstrip()

    if not additional_details:
        additional_details = "[No additional details provided]"

    report = f"""\
{now}
Hello, I am CrashSalvato, your friendly ProjectEben crash handler
Created by {variant}

******* HW INFO *******
Model: {model}
Serial: {serial}
Revision: {revision}

******* SW INFO *******
Kernel: { _get_kernel_version() }
OS-release: { _get_os_release() }

******* CALLER INFORMATION *******
PID of process: {caller_pid}
Process name: {process_name}
Command line: {command_line}

******* SYSTEM STATE *******
System started at {boot_time}

Current user: {current_user}
Hostname: {hostname}

Our kernel cmdline for this session was the following:
{cmdline}

Running Processes:
{processes}

******* LOGS *******
Videocore message:
{vc_message}

Videocore assert:
{vc_assert}

Dmesg last 30 lines:
{dmesg}

Journal last 30 lines:
{journal}

******* ADDITIONAL DETAILS! *******
These details were provided by the program and can help diagnose the issue.

{additional_details}
"""

    return report