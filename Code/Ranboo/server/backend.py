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
from typing import List, Optional, Sequence, Union
import mimetypes
import os
import platform
import re
import shutil
import signal
import subprocess
import time
import webbrowser
from pathlib import Path
import psutil
import stat as stat_module
from datetime import datetime
import shlex


class HyprlandNotSupportedError(RuntimeError):
    """Raised when a Hyprland-only operation is requested on another desktop."""

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
        self._hyprland_instance_signature: Optional[str] = None
        self._ignored_hyprland_signatures: set[str] = set()

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


    def _split_on_semicolon(self, command: List[str]) -> List[List[str]]:
        """
        Split a command list on literal ';' tokens, like Bash.
        """
        groups = []
        current = []

        for token in command:
            if token == ";":
                if current:
                    groups.append(current)
                    current = []
            else:
                current.append(token)

        if current:
            groups.append(current)

        return groups

    def simple_tokenize(self, command: Union[str, List[str]]) -> List[str]:
        """
        Very simple shell-like tokenizer.
        - Splits on whitespace
        - Preserves quoted strings
        - Treats ';' as its own token
        """
        if isinstance(command, list):
            return list(command)

        tokens = []
        current = []
        quote = None  # None, "'" or '"'

        for ch in command:
            if quote:
                if ch == quote:
                    quote = None
                else:
                    current.append(ch)
                continue

            if ch in ("'", '"'):
                quote = ch
                continue

            if ch == ";":
                if current:
                    tokens.append("".join(current))
                    current = []
                tokens.append(";")
                continue

            if ch.isspace():
                if current:
                    tokens.append("".join(current))
                    current = []
                continue

            current.append(ch)

        if current:
            tokens.append("".join(current))

        return tokens

    def _run(self, command, dry_run=False) -> CommandResult:
        tokens = self.simple_tokenize(command)
        command_tuple = tuple(tokens)
        groups = self._split_on_semicolon(tokens)

        if dry_run:
            print(f"Would run (bash ;): {groups}")
            return CommandResult(command_tuple, 0, "", "")

        print(f"running command with bash ';' semantics: {groups}")

        final_returncode = 0
        stdout_parts = []
        stderr_parts = []

        for group in groups:
            print(f"running command: {tuple(group)}")

            completed = subprocess.run(
                tuple(group),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

            final_returncode = completed.returncode

            if completed.stdout:
                stdout_parts.append(completed.stdout.rstrip())
            if completed.stderr:
                stderr_parts.append(completed.stderr.rstrip())

        return CommandResult(
            command=command_tuple,
            returncode=final_returncode,
            stdout="\n".join(stdout_parts),
            stderr="\n".join(stderr_parts),
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

    def pmic_temperature_c(self) -> float | int | None:
        """Return PMIC temperature in Celsius, or None if unavailable.

        Expected output from vcgencmd:
            temp=48.0'C
        """
        if self.dry_run:
            return 0
        result = self._run(["vcgencmd", "measure_temp", "pmic"])
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

    def throttled(self) -> dict:
        """
        Get and parse the Raspberry Pi throttling state using vcgencmd.

        Returns:
            dict: The raw value plus the current and historical throttle states.
        """

        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            check=True
        )

        # Example output:
        # throttled=0x50005
        output = result.stdout.strip()

        if not output.startswith("throttled="):
            raise ValueError(f"Unexpected vcgencmd output: {output!r}")

        value = int(output.split("=", 1)[1], 16)

        return {
            "raw": value,
            "raw_hex": f"0x{value:x}",

            # Current conditions
            "undervoltage_detected": bool(value & 0x1),
            "arm_frequency_capped": bool(value & 0x2),
            "currently_throttled": bool(value & 0x4),
            "soft_temperature_limit_active": bool(value & 0x8),

            # Historical conditions
            "undervoltage_has_occurred": bool(value & 0x10000),
            "arm_frequency_capping_has_occurred": bool(value & 0x20000),
            "throttling_has_occurred": bool(value & 0x40000),
            "soft_temperature_limit_has_occurred": bool(value & 0x80000),
        }

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
        if self.dry_run:
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

        self._wayvnc_proc = self._start_wayvnc()

        return self._wayvnc_proc.pid

    def start_wayvnc_websocket(self) -> Optional[int]:
        """Start wayvnc with websocket support (-w)."""
        if self._wayvnc_proc and self._wayvnc_proc.poll() is None:
            return self._wayvnc_proc.pid

        self._wayvnc_proc = self._start_wayvnc(websocket=True)

        return self._wayvnc_proc.pid

    @staticmethod
    def _hyprland_processes() -> list[psutil.Process]:
        """Return the current user's Hyprland compositor processes."""
        processes = []
        try:
            uid = os.getuid()
            for process in psutil.process_iter(["name", "cmdline", "uids"]):
                try:
                    process_uids = process.info.get("uids")
                    if process_uids is not None and process_uids.real != uid:
                        continue

                    name = process.info.get("name") or ""
                    cmdline = process.info.get("cmdline") or []
                    if name.lower() == "hyprland" or any(
                        argument and Path(argument).name.lower() == "hyprland"
                        for argument in cmdline
                    ):
                        processes.append(process)
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError):
            pass
        return processes

    def _hyprland_running(self) -> bool:
        """Return whether a Hyprland compositor is running for this user."""
        return bool(self._hyprland_processes())

    @staticmethod
    def _hyprland_runtime_dir(environment: Optional[dict[str, str]] = None) -> Path:
        if environment is None:
            environment = os.environ
        runtime_dir = environment.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return Path(runtime_dir) / "hypr"

    @staticmethod
    def _hyprland_process_signature(process: psutil.Process) -> Optional[str]:
        """Read a Hyprland instance signature directly from a process."""
        try:
            return process.environ().get("HYPRLAND_INSTANCE_SIGNATURE") or None
        except (psutil.Error, OSError):
            return None

    def _hyprland_signatures(self) -> list[str]:
        """Return instance signatures currently present in Hyprland's runtime dir."""
        hypr_dir = self._hyprland_runtime_dir()
        try:
            return sorted(path.name for path in hypr_dir.iterdir() if path.is_dir())
        except (FileNotFoundError, PermissionError, OSError):
            return []

    def _current_hyprland_signature(self) -> Optional[str]:
        """Resolve the active signature, preferring the instance we last selected."""
        available = [
            signature
            for signature in self._hyprland_signatures()
            if signature not in self._ignored_hyprland_signatures
        ]
        if self._hyprland_instance_signature in available:
            return self._hyprland_instance_signature

        for process in self._hyprland_processes():
            signature = self._hyprland_process_signature(process)
            if signature in available:
                self._hyprland_instance_signature = signature
                return signature

        inherited = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if inherited in available:
            self._hyprland_instance_signature = inherited
            return inherited

        if available:
            self._hyprland_instance_signature = available[0]
            return available[0]
        return None

    def _hyprland_environment(self) -> dict[str, str]:
        """Build the environment needed to attach wayvnc to Hyprland."""
        environment = os.environ.copy()
        runtime_dir = Path(environment.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        environment["XDG_RUNTIME_DIR"] = str(runtime_dir)

        signature = self._current_hyprland_signature()
        if signature is None:
            raise RuntimeError(f"No Hyprland instance found in {runtime_dir / 'hypr'}")
        environment["HYPRLAND_INSTANCE_SIGNATURE"] = signature

        displays = sorted(
            path.name
            for path in runtime_dir.glob("wayland-*")
            if path.is_socket()
        )
        if not displays:
            raise RuntimeError(f"No Wayland display found in {runtime_dir}")
        environment["WAYLAND_DISPLAY"] = displays[0]
        return environment

    def restart_hyprland(self) -> dict:
        """Kill Hyprland and select the replacement compositor's new signature."""
        processes = self._hyprland_processes()
        if not processes:
            raise HyprlandNotSupportedError(
                "Hyprland is not in use; compositor restart is not supported"
            )

        old_signatures = {
            signature
            for process in processes
            if (signature := self._hyprland_process_signature(process)) is not None
        }
        if self._hyprland_instance_signature:
            old_signatures.add(self._hyprland_instance_signature)

        signatures_before_restart = set(self._hyprland_signatures())
        if not old_signatures and len(signatures_before_restart) == 1:
            old_signatures.update(signatures_before_restart)
        ignored_signatures = signatures_before_restart | old_signatures

        old_pids = [process.pid for process in processes]
        for process in processes:
            try:
                process.kill()
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
            except (psutil.Error, OSError) as exc:
                raise RuntimeError(
                    f"Unable to kill Hyprland process {process.pid}: {exc}"
                ) from exc
        self._ignored_hyprland_signatures.update(ignored_signatures)

        # The desktop session is expected to supervise Hyprland and launch its
        # replacement. Do not inspect runtime state until it has had five seconds.
        time.sleep(5)

        hypr_dir = self._hyprland_runtime_dir()
        for signature in old_signatures:
            try:
                shutil.rmtree(hypr_dir / signature)
            except FileNotFoundError:
                pass
            except OSError:
                # Runtime cleanup is best-effort; signature exclusion below is
                # the safety mechanism that prevents stale state from being used.
                pass

        replacement_processes = [
            process
            for process in self._hyprland_processes()
            if process.pid not in old_pids
        ]
        if not replacement_processes:
            raise RuntimeError("Hyprland did not restart after 5 seconds")

        available_signatures = set(self._hyprland_signatures()) - ignored_signatures
        new_signature = None
        for process in replacement_processes:
            signature = self._hyprland_process_signature(process)
            if signature in available_signatures:
                new_signature = signature
                break

        if new_signature is None:
            new_signature = next(
                (
                    signature
                    for signature in self._hyprland_signatures()
                    if signature not in ignored_signatures
                ),
                None,
            )

        if new_signature is None:
            raise RuntimeError("Hyprland restarted without a new instance signature")

        self._hyprland_instance_signature = new_signature
        return {
            "old_pids": old_pids,
            "new_pid": replacement_processes[0].pid,
            "old_signatures": sorted(old_signatures),
            "instance_signature": new_signature,
        }

    def _start_wayvnc(self, websocket: bool = False) -> subprocess.Popen:
        """Start wayvnc, adding Hyprland's headless output setup when needed."""
        command = ["wayvnc"]
        environment = None

        if self._hyprland_running():
            environment = self._hyprland_environment()
            subprocess.run(
                ["hyprctl", "output", "create", "headless", "VNC-1"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            command.extend(["--log-level=error", "--disable-resizing"])
            command.extend(["-g", "-o", "VNC-1"])

        if environment:
            command.extend(["-w"] if websocket else [])
            command.extend(["0.0.0.0", "5900"])
        else:
            command.extend(["-g", "0.0.0.0"])
            command.extend(["-w"] if websocket else [])
        return subprocess.Popen(command, env=environment)

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
                self._wayvnc_proc.wait()

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
                self._novnc_proc.wait()

        return True

    @staticmethod
    def _vnc_process_status(process: Optional[subprocess.Popen]) -> dict:
        """Return a stable status payload for a tracked VNC process."""
        returncode = process.poll() if process is not None else None
        running = process is not None and returncode is None

        return {
            "running": running,
            "pid": process.pid if running else None,
            "exit_code": returncode if returncode is not None and returncode >= 0 else None,
            "signal": -returncode if returncode is not None and returncode < 0 else None,
        }

    def get_vnc_status(self) -> dict:
        """Get the status of wayvnc and noVNC processes."""
        wayvnc = self._vnc_process_status(self._wayvnc_proc)

        # noVNC is launched as wayvnc's companion.  Do not leave a stale proxy
        # behind if wayvnc exits unexpectedly.
        if (
            self._wayvnc_proc is not None
            and not wayvnc["running"]
            and self._novnc_proc is not None
            and self._novnc_proc.poll() is None
        ):
            self.stop_novnc()

        return {
            "wayvnc": wayvnc,
            "novnc": self._vnc_process_status(self._novnc_proc),
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
        return self._run(
            f"sudo timedatectl set-time \'{datetime_str}\'"
        )

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

    def screenshot(self) -> dict:
        """Take a screenshot using the `grim` command and return the image data."""
        result = self._run(["grim", "-"])
        if not result.ok:
            raise RuntimeError(f"Failed to take screenshot: {result.stderr}")

        return {
            "data": result.stdout.encode("latin1"),  # binary data as bytes
            "content_type": "image/png",
            "size_bytes": len(result.stdout),
        }

    def get_specialty_device_type(self):
        """Get the specialty type of the device the Ranboo server is running on."""
        # Valid device types as of right now are as follows:
        # "Unknown.Generic": anything that does not fall into other form factors
        # "Eben.Generic": Generic devices that we know are used for Project Eben
        # "Deletescape.Generic": Generic devices running deletescapeOS
        # "Eben.Stockwood": Project Stockwood devices
        # "Eben.Stockwood.Nirav": Project Nirav devices
        # "Eben.Ironmouse": Project Ironmouse devices
        # "Lumon": Project Lumon devices
        # "Deletescape.Phone": phones running deletescapeOS
        # "Deletescape.Tablet": tablets running deletescapeOS
        # "Deletescape.Desk": desktops and laptops running deletescapeOS

        ## TODO: Get actual type
        return "Eben.Stockwood"

    def battery_status(self) -> dict:
        """Get battery status information using psutil."""
        try:
            battery = psutil.sensors_battery()
            if battery is None:
                return {"has_battery": False}

            return {
                "has_battery": True,
                "percent": battery.percent,
                "secsleft": battery.secsleft,
                "power_plugged": battery.power_plugged,
            }
        except Exception as e:
            return {
                "has_battery": False,
                "error": str(e),
            }

    def get_disp_brightness(self):
        "Get the display brightness"
        # In practice, we would need to somehow get/set the display's brightness
        # Most likely through some sort of microcontroller
        # So we stub this for now
        return "255"

    def set_disp_brightness(self):
        "Set the display brightness"
        # In practice, we would need to somehow get/set the display's brightness
        # Most likely through some sort of microcontroller
        # So we stub this for now
        return True

    def enable_ap(self) -> CommandResult:
        """Enable the access point"""
        return self._run(["sudo", "rpi-hotspot", "start-ap"])

    def disable_ap(self) -> CommandResult:
        """Disable the access point"""
        return self._run(["sudo", "rpi-hotspot", "stop-ap"])

    def enable_eth_share(self) -> CommandResult:
        """Enable the ethernet sharing"""
        return self._run(["sudo", "rpi-hotspot", "enable-eth-share"])

    def disable_eth_share(self) -> CommandResult:
        """Disable the ethernet sharing"""
        return self._run(["sudo", "rpi-hotspot", "disable-eth-share"])

    THERMAL_ZONE_MODE_PATH = Path("/sys/class/thermal/thermal_zone0/mode")
    THERMAL_CLASS_PATH = Path("/sys/class/thermal")

    def _sudo_sysfs_write(self, path: Path, value: str) -> None:
        """Write a value to sysfs using sudo + tee.

        Shell redirection such as:
            sudo echo VALUE > FILE

        does not work because the shell performing the redirection is
        still unprivileged. `sudo tee` performs the actual open/write
        as root.
        """
        result = subprocess.run(
            ["sudo", "-n", "tee", str(path)],
            input=f"{value}\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise PermissionError(
                result.stderr.strip()
                or f"Unable to write {value!r} to {path}"
            )

    def set_fan_governor(self, enabled: bool) -> bool:
        """Enable or disable kernel thermal control."""
        mode = "enabled" if enabled else "disabled"

        if self.dry_run:
            print(f"Would set thermal governor to {mode}")
            return True

        if not self.THERMAL_ZONE_MODE_PATH.exists():
            raise FileNotFoundError(
                f"Thermal governor interface not found: "
                f"{self.THERMAL_ZONE_MODE_PATH}"
            )

        self._sudo_sysfs_write(
            self.THERMAL_ZONE_MODE_PATH,
            mode,
        )

        actual = self.THERMAL_ZONE_MODE_PATH.read_text(
            encoding="utf-8"
        ).strip()

        return actual == mode

    def enable_fan_governor(self) -> bool:
        """Give fan control back to the kernel thermal governor."""
        return self.set_fan_governor(True)

    def disable_fan_governor(self) -> bool:
        """Disable the governor so userspace can control the fan."""
        return self.set_fan_governor(False)

    def get_fan_governor(self) -> str | None:
        """Return the thermal governor state."""
        if self.dry_run:
            return "enabled"

        try:
            return self.THERMAL_ZONE_MODE_PATH.read_text(
                encoding="utf-8"
            ).strip()
        except (FileNotFoundError, OSError):
            return None

    def get_fan_control(self) -> str | None:
        """Return whether the governor or userspace controls the fan."""
        mode = self.get_fan_governor()

        if mode == "enabled":
            return "governor"

        if mode == "disabled":
            return "userspace"

        return None

    def _find_fan_cooling_device(self) -> Path:
        """Locate the fan cooling device exposed through sysfs."""
        for device in sorted(
            self.THERMAL_CLASS_PATH.glob("cooling_device*")
        ):
            try:
                device_type = (
                    (device / "type")
                    .read_text(encoding="utf-8")
                    .strip()
                    .lower()
                )
            except OSError:
                continue

            if "fan" in device_type:
                return device

        raise FileNotFoundError("No fan cooling device found")

    def get_fan_state(self) -> int | None:
        """Return the current fan cooling state."""
        if self.dry_run:
            return 0

        device = self._find_fan_cooling_device()

        try:
            return int(
                (device / "cur_state")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (FileNotFoundError, ValueError, OSError):
            return None

    def get_fan_max_state(self) -> int | None:
        """Return the maximum supported fan cooling state."""
        if self.dry_run:
            return 4

        device = self._find_fan_cooling_device()

        try:
            return int(
                (device / "max_state")
                .read_text(encoding="utf-8")
                .strip()
            )
        except (FileNotFoundError, ValueError, OSError):
            return None

    def set_fan_state(self, state: int) -> bool:
        """Set the fan cooling state using a privileged sysfs write."""
        if not isinstance(state, int):
            raise TypeError("Fan state must be an integer")

        if self.dry_run:
            print(f"Would set fan state to {state}")
            return True

        device = self._find_fan_cooling_device()

        cur_state = device / "cur_state"
        max_state_path = device / "max_state"

        max_state = int(
            max_state_path.read_text(
                encoding="utf-8"
            ).strip()
        )

        if not 0 <= state <= max_state:
            raise ValueError(
                f"Fan state must be between 0 and {max_state}"
            )

        self._sudo_sysfs_write(cur_state, str(state))

        actual = int(
            cur_state.read_text(
                encoding="utf-8"
            ).strip()
        )

        return actual == state

    def logout(self):
        session_id = os.environ.get("XDG_SESSION_ID")

        if not session_id:
            raise RuntimeError("Could not determine the current session")

        subprocess.run(
            ["loginctl", "terminate-session", session_id],
            check=True
        )