#!/usr/bin/env python3
"""Raspberry Pi system utilities.

This module provides a small, Pythonic interface for querying Raspberry Pi
system information and performing a few administrative actions.

Design goals:
- Consistent return types
- Structured data instead of ad-hoc strings
- Minimal surprises across platforms
- Clear docstrings and type hints
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
import mimetypes
import os
import platform
import re
import shutil
import signal
import subprocess
import webbrowser
from tkinter import Tk, filedialog
from pathlib import Path
import psutil
import stat as stat_module
from datetime import datetime

@dataclass(frozen=True)
class CommandResult:
    """Result of a command execution.

    Attributes:
        command: The command that was executed.
        returncode: Process return code (0 means success).
        stdout: Standard output text, stripped of trailing whitespace.
        stderr: Standard error text, stripped of trailing whitespace.
    """

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Return True if the command completed successfully."""
        return self.returncode == 0


@dataclass(frozen=True)
class DiskUsage:
    """Disk usage statistics in gibibytes."""

    total_gib: float
    used_gib: float
    free_gib: float


@dataclass(frozen=True)
class MemoryUsage:
    """Memory usage statistics in mebibytes."""

    total_mib: int
    used_mib: int
    free_mib: int


@dataclass(frozen=True)
class ProcessInfo:
    """Basic process information."""

    pid: int
    command: str
    cpu_percent: float
    mem_percent: float

@dataclass(frozen=True)
class Identification:
    """Basic process information."""

    machine: str
    boot: str

class Api:
    """Small helper for Raspberry Pi / Linux host system queries.

    This class wraps a handful of shell commands and filesystem reads behind
    a stable, typed API.

    Args:
        dry_run: If True, commands are not executed. If None, dry-run mode is
            enabled automatically on non-POSIX systems.
    """

    CPU_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
    HOME_DIR = Path.home().resolve()
    MAX_FILE_BYTES = 512 * 1024 * 1024

    def __init__(self, dry_run: Optional[bool] = None) -> None:
        self.dry_run = False
        self._wayvnc_proc: Optional[subprocess.Popen] = None
        self._novnc_proc: Optional[subprocess.Popen] = None

    def _resolve_home_path(self, path_value: str) -> Path:
        """Resolve a user supplied path and enforce the home-directory boundary."""
        candidate = Path(path_value).expanduser()
        if not candidate.is_absolute():
            candidate = self.HOME_DIR / candidate

        resolved = candidate.resolve(strict=False)
        home = self.HOME_DIR
        if resolved != home and home not in resolved.parents:
            raise PermissionError("Path must stay within the home directory")

        return resolved

    def _content_type_for_path(self, path: Path) -> str:
        mime, _ = mimetypes.guess_type(str(path))
        return mime or "application/octet-stream"

    def _run(self, command: Sequence[str]) -> CommandResult:
        """Run a command and return a structured result.

        In dry-run mode, this does not execute anything and returns a successful
        stub result with empty output.

        Args:
            command: Command and arguments to execute.

        Returns:
            A CommandResult object.
        """
        command_tuple = tuple(command)

        if self.dry_run:
            print(f"Would run {command}")
            return CommandResult(
                command=command_tuple,
                returncode=0,
                stdout="",
                stderr="",
            )
        print(f"running command {command}")
        completed = subprocess.run(
            command_tuple,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return CommandResult(
            command=command_tuple,
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )

    @staticmethod
    def hostname() -> str:
        """Return the current hostname."""
        return platform.node()

    def uptime(self) -> str | int | None:
        """Return the raw uptime, or None if unavailable."""
        if self.dry_run:
            return 0
        try:
            with open("/proc/uptime", "r") as f:
                return f.read().strip("\x00")
        except:
            return None

        return None
    def cpu_temperature_c(self) -> float | int | None:
        """Return CPU temperature in Celsius, or None if unavailable."""
        if self.dry_run:
            return 0
        try:
            raw = self.CPU_TEMP_PATH.read_text(encoding="utf-8").strip()
            return int(raw) / 1000.0
        except (FileNotFoundError, ValueError, OSError):
            return None

    def gpu_temperature_c(self) -> float | int | None:
        """Return GPU temperature in Celsius, or None if unavailable.

        Expected output from vcgencmd:
            temp=48.0'C
        """
        if self.dry_run:
            return 0
        result = self._run(["vcgencmd", "measure_temp"])
        if not result.ok:
            return None

        match = re.search(r"temp=([\d.]+)", result.stdout)
        return float(match.group(1)) if match else None

    def voltage_v(self) -> float | int | None:
        """Return core voltage in volts, or None if unavailable.

        Expected output from vcgencmd:
            volt=0.8438V
        """
        if self.dry_run:
            return 0
        result = self._run(["vcgencmd", "measure_volts"])
        if not result.ok:
            return None

        match = re.search(r"volt=([\d.]+)", result.stdout)
        return float(match.group(1)) if match else None

    def usb_devices(self) -> list:
        """Return attached USB devices as a list of lines."""
        result = self._run(["lsusb"])
        if not result.ok or not result.stdout:
            return []
        return result.stdout.splitlines()

    def processes(self) -> list:
        """Return process information for all running processes.

        Uses:
            ps -eo pid=,comm=,%cpu=,%mem=

        Returns:
            A list of ProcessInfo objects. Invalid lines are skipped.
        """
        result = self._run(["ps", "-eo", "pid=,comm=,%cpu=,%mem="])
        if not result.ok or not result.stdout:
            return []

        parsed: list[ProcessInfo] = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) != 4:
                continue
            try:
                pid = int(parts[0])
                command = parts[1]
                cpu_percent = float(parts[2])
                mem_percent = float(parts[3])
            except ValueError:
                continue

            parsed.append(
                ProcessInfo(
                    pid=pid,
                    command=command,
                    cpu_percent=cpu_percent,
                    mem_percent=mem_percent,
                )
            )
        return parsed

    def memory_usage(self) -> MemoryUsage | None:
        """Return system memory usage in MiB, or None if unavailable."""
        result = self._run(["free", "-m"])
        if not result.ok or not result.stdout:
            return None

        lines = result.stdout.splitlines()
        if len(lines) < 2:
            return None

        try:
            mem_fields = lines[1].split()
            return MemoryUsage(
                total_mib=int(mem_fields[1]),
                used_mib=int(mem_fields[2]),
                free_mib=int(mem_fields[3]),
            )
        except (IndexError, ValueError):
            return None

    def kill_process(self, pid: int, sig: int = signal.SIGILL) -> CommandResult:
        """Terminate a process by PID.

        Args:
            pid: Process ID to terminate.
            sig: Signal number to send. Defaults to SIGKILL.

        Returns:
            A CommandResult from the `kill` command.
        """
        return self._run(["kill", f"-{sig}", str(pid)])

    def poweroff(self) -> CommandResult:
        """Power off the system immediately."""
        return self._run(["sudo", "shutdown", "-h", "now"])

    def reboot(self) -> CommandResult:
        """Reboot the system immediately."""
        return self._run(["sudo", "reboot"])
    
    def softreboot(self) -> CommandResult:
        """Softly reboot the system immediately."""
        return self._run([
            "sudo",
            "systemctl",
            "start",
            "soft-reboot.target",
            "--job-mode=replace-irreversibly",
            "--no-block",
        ])
    
    def poweroff(self) -> CommandResult:
        """Power off the system immediately."""
        return self._run(["sudo", "shutdown", "-h", "now"])
    
    def sleep(self) -> CommandResult:
        """Put the system to sleep immediately."""
        return self._run(["sudo", "systemctl", "sleep"])
    
    def suspend(self) -> CommandResult:
        """Suspend the system immediately."""
        return self._run(["sudo", "systemctl", "suspend"])
    
    def hibernate(self) -> CommandResult:
        """Hibernate the system immediately."""
        return self._run(["sudo", "systemctl", "hibernate"])
    
    def model(self) -> str:
        if self.dry_run:
            return "stub-model"
        try:
            with open("/proc/device-tree/model", "r") as f:
                return f.read().strip("\x00")
        except:
            return "unknown"
        
    def cmdline(self) -> str:
        if self.dry_run:
            return "not a linux kernel"
        try:
            with open("/proc/cmdline", "r") as f:
                return f.read().strip("\x00")
        except:
            return "unknown"
        
    def linux_ver(self) -> str:
        if self.dry_run:
            return "not a linux kernel"
        try:
            with open("/proc/version", "r") as f:
                return f.read().strip("\x00")
        except:
            return "unknown"
        
    def serial(self) -> str:
        if self.dry_run:
            return "STUBSRLXXXXX"
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("Serial"):
                        return line.split(":")[1].strip()
        except:
            return "unknown"

        return "unknown"
    
    def revision(self) -> str:
        if self.dry_run != "posix":
            return "stub"
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("Revision"):
                        return line.split(":")[1].strip()
        except:
            return "unknown"

        return "unknown"

    def disk_info(self):
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
    
    def start_wayvnc(self) -> Optional[int]:
        """Start wayvnc normally (no websocket)."""
        if self._wayvnc_proc and self._wayvnc_proc.poll() is None:
            return self._wayvnc_proc.pid  # already running

        self._wayvnc_proc = subprocess.Popen([
            "wayvnc",
            "-g", "0.0.0.0"
        ])

        return self._wayvnc_proc.pid
    
    def start_wayvnc_websocket(self) -> Optional[int]:
        """Start wayvnc with websocket support (-w)."""
        if self._wayvnc_proc and self._wayvnc_proc.poll() is None:
            return self._wayvnc_proc.pid

        self._wayvnc_proc = subprocess.Popen([
            "wayvnc",
            "-g", "0.0.0.0",
            "-w"
        ])

        return self._wayvnc_proc.pid
    
    def start_wayvnc_with_novnc(self) -> dict:
        """Start wayvnc and noVNC proxy."""
        result = {}

        # Start wayvnc first
        result["wayvnc_pid"] = self.start_wayvnc()

        # Give it a moment (simple + reliable)
        import time
        time.sleep(2)

        # Start noVNC
        if not (self._novnc_proc and self._novnc_proc.poll() is None):
            novnc_path = os.path.join(os.environ["HOME"], "noVNC")

            self._novnc_proc = subprocess.Popen(
                ["./utils/novnc_proxy", "--vnc", "localhost:5900"],
                cwd=novnc_path
            )

        result["novnc_pid"] = self._novnc_proc.pid
        return result
    
    def stop_wayvnc(self) -> bool:
        """Stop the running wayvnc process."""
        if not self._wayvnc_proc:
            return False

        if self._wayvnc_proc.poll() is None:
            self._wayvnc_proc.terminate()
            try:
                self._wayvnc_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._wayvnc_proc.kill()

        self._wayvnc_proc = None
        return True
    
    def stop_novnc(self) -> bool:
        """Stop the running noVNC proxy."""
        if not self._novnc_proc:
            return False

        if self._novnc_proc.poll() is None:
            self._novnc_proc.terminate()
            try:
                self._novnc_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._novnc_proc.kill()

        self._novnc_proc = None
        return True

    def get_vnc_status(self) -> dict:
        """Get the status of wayvnc and noVNC processes."""
        wayvnc_running = self._wayvnc_proc and self._wayvnc_proc.poll() is None
        novnc_running = self._novnc_proc and self._novnc_proc.poll() is None

        return {
            "wayvnc": {
                "running": wayvnc_running,
                "pid": self._wayvnc_proc.pid if wayvnc_running else None,
            },
            "novnc": {
                "running": novnc_running,
                "pid": self._novnc_proc.pid if novnc_running else None,
            }
        }

    def set_time(self, datetime_str: str) -> CommandResult:
        """Set the system date and time.

        Args:
            datetime_str: Date/time string in format "YYYY-MM-DD HH:MM:SS"

        Example:
            set_time("2026-04-09 14:30:00")

        Returns:
            CommandResult from timedatectl.
        """
        # Set the system time
        return self._run([
            "sudo",
            "timedatectl",
            "set-time",
            datetime_str
        ])

    def upload_file(self, target_path: str, data: bytes) -> dict:
        """Write a file under the home directory.

        Args:
            target_path: File path relative to the home directory or an absolute
                path inside the home directory.
            data: File contents to write.
        """
        if len(data) > self.MAX_FILE_BYTES:
            raise ValueError("File exceeds the 512 MB limit")

        target = self._resolve_home_path(target_path)
        if target.exists() and target.is_dir():
            raise IsADirectoryError(str(target))

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        return {
            "path": str(target),
            "bytes_written": len(data),
            "content_type": self._content_type_for_path(target),
        }

    def download_file(self, source_path: str) -> dict:
        """Read a file under the home directory.

        Args:
            source_path: File path relative to the home directory or an absolute
                path inside the home directory.
        """
        source = self._resolve_home_path(source_path)
        if not source.exists():
            raise FileNotFoundError(str(source))
        if source.is_dir():
            raise IsADirectoryError(str(source))

        size = source.stat().st_size
        if size > self.MAX_FILE_BYTES:
            raise ValueError("File exceeds the 512 MB limit")

        return {
            "path": str(source),
            "data": source.read_bytes(),
            "content_type": self._content_type_for_path(source),
            "size_bytes": size,
        }
    
    def open_website(self, url: str):
        try:
            wb = webbrowser.open(url)
            if wb:
                return {
                    "ok": True
                }
            else:
                return {
                    "ok": False
                }
        except Exception as e:
            return {
                "ok": False,
                "error": e
            }
