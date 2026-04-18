from __future__ import annotations

import datetime
import json
import linecache
import os
import platform
import re
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Optional

# ANSI colors
RED = "\033[91m"
YELLOW = "\033[93m"
WHITE = "\033[97m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _safe_repr(value: Any, max_len: int = 1000) -> str:
    try:
        text = repr(value)
    except Exception as exc:
        text = f"<repr failed: {type(exc).__name__}: {exc}>"

    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _normalize_path(path: str | Path) -> Path:
    return path if isinstance(path, Path) else Path(path)


@dataclass
class CrashReporterConfig:
    # Files / output
    log_dir: str | Path = "crash_logs"
    save_json: bool = True
    print_console: bool = True

    # Context / detail
    context_radius: int = 2  # +/- lines around failing line
    capture_globals: bool = False
    capture_environment: bool = False
    capture_python_path: bool = True
    capture_argv: bool = True
    repr_max_len: int = 1000

    # Hooks
    install_sys_excepthook: bool = True
    install_threading_excepthook: bool = True

    # Handled exceptions (via debugger trace events)
    capture_handled_exceptions: bool = False
    handled_console_output: bool = False
    handled_save_json: bool = True

    # Filtering
    exclude_exception_types: tuple[type[BaseException], ...] = (
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
    )

    # Optional: restrict handled-exception reports to files under current working directory
    handled_only_user_code: bool = True


class CrashReporter:
    """
    Installable crash reporter.

    Features:
    - Unhandled exceptions via sys.excepthook
    - Thread exceptions via threading.excepthook
    - Optional handled exception observation via sys.settrace/threading.settrace
    """

    def __init__(self, config: Optional[CrashReporterConfig] = None):
        self.config = config or CrashReporterConfig()
        self.log_dir = _normalize_path(self.config.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._previous_sys_excepthook = None
        self._previous_threading_excepthook = None
        self._installed = False

        # Used to suppress duplicate handled-exception reports from trace events
        self._seen_handled: set[tuple[int, str, int]] = set()

        # Cache cwd for faster user-code filtering
        try:
            self._cwd = str(Path.cwd().resolve())
        except Exception:
            self._cwd = os.getcwd()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def install(self) -> "CrashReporter":
        if self._installed:
            return self

        if self.config.install_sys_excepthook:
            self._previous_sys_excepthook = sys.excepthook
            sys.excepthook = self._sys_excepthook

        if self.config.install_threading_excepthook and hasattr(threading, "excepthook"):
            self._previous_threading_excepthook = threading.excepthook
            threading.excepthook = self._threading_excepthook

        if self.config.capture_handled_exceptions:
            sys.settrace(self._trace_dispatch)
            threading.settrace(self._trace_dispatch)

        self._installed = True
        return self

    def uninstall(self) -> None:
        if not self._installed:
            return

        if self.config.install_sys_excepthook and self._previous_sys_excepthook is not None:
            sys.excepthook = self._previous_sys_excepthook

        if (
            self.config.install_threading_excepthook
            and self._previous_threading_excepthook is not None
            and hasattr(threading, "excepthook")
        ):
            threading.excepthook = self._previous_threading_excepthook

        if self.config.capture_handled_exceptions:
            sys.settrace(None)
            threading.settrace(None)

        self._installed = False

    # -------------------------------------------------------------------------
    # Hooks
    # -------------------------------------------------------------------------

    def _sys_excepthook(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._should_ignore(exc_type):
            if self._previous_sys_excepthook:
                self._previous_sys_excepthook(exc_type, exc_value, exc_tb)
            return

        self._report_exception(
            exc_type=exc_type,
            exc_value=exc_value,
            exc_tb=exc_tb,
            handled=False,
            source="sys.excepthook",
        )

    def _threading_excepthook(self, args: threading.ExceptHookArgs) -> None:
        exc_type = args.exc_type
        exc_value = args.exc_value
        exc_tb = args.exc_traceback

        if self._should_ignore(exc_type):
            if self._previous_threading_excepthook:
                self._previous_threading_excepthook(args)
            return

        self._report_exception(
            exc_type=exc_type,
            exc_value=exc_value,
            exc_tb=exc_tb,
            handled=False,
            source=f"threading.excepthook(thread={getattr(args.thread, 'name', 'unknown')})",
        )

    def _trace_dispatch(self, frame: FrameType, event: str, arg: Any):
        if event == "exception":
            try:
                exc_type, exc_value, exc_tb = arg
                if not self._should_ignore(exc_type):
                    if self._should_report_handled(frame, exc_tb):
                        key = self._handled_key(exc_value, exc_tb, frame)
                        if key not in self._seen_handled:
                            self._seen_handled.add(key)
                            self._report_exception(
                                exc_type=exc_type,
                                exc_value=exc_value,
                                exc_tb=exc_tb,
                                handled=True,
                                source="sys.settrace",
                                current_frame=frame,
                            )
            except Exception:
                # Crash reporter should not become the crash.
                pass

        return self._trace_dispatch

    # -------------------------------------------------------------------------
    # Reporting
    # -------------------------------------------------------------------------

    def _report_exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: Optional[TracebackType],
        handled: bool,
        source: str,
        current_frame: Optional[FrameType] = None,
    ) -> None:
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        kind = "err" if handled else "panic"
        log_file = self.log_dir / f"{kind}_{ts}.json"

        system_info = self._build_system_info(ts)
        frames = self._extract_frames(exc_tb, current_frame=current_frame)

        crash_data = {
            "kind": kind,
            "source": source,
            "exception_type": exc_type.__name__,
            "exception_message": str(exc_value),
            "system_info": system_info,
            "frames": frames,
        }

        if self._should_print_console(handled):
            self._print_console_report(crash_data)

        if self._should_save_json(handled):
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(crash_data, f, indent=4)
                if self.config.print_console or (handled and self.config.handled_console_output):
                    print(f"{YELLOW}{kind} exception dump saved to {log_file}{RESET}")
            except Exception as save_exc:
                if self.config.print_console or (handled and self.config.handled_console_output):
                    print(f"{RED}failed to save crash dump: {save_exc}{RESET}")

    def _build_system_info(self, ts: str) -> dict[str, Any]:
        info = {
            "timestamp": ts,
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
            "python_build": platform.python_build(),
            "python_compiler": platform.python_compiler(),
            "cwd": os.getcwd(),
            "thread_name": threading.current_thread().name,
            "thread_ident": threading.get_ident(),
        }

        if self.config.capture_python_path:
            info["python_path"] = list(sys.path)

        if self.config.capture_argv:
            info["argv"] = list(sys.argv)

        if self.config.capture_environment:
            info["environment"] = dict(os.environ)

        return info

    def _extract_frames(
        self,
        exc_tb: Optional[TracebackType],
        current_frame: Optional[FrameType] = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        # Preferred path: use traceback frames directly.
        tb = exc_tb
        while tb is not None:
            frame = tb.tb_frame
            lineno = tb.tb_lineno
            result.append(self._frame_info(frame, lineno))
            tb = tb.tb_next

        # Handled exception trace events can sometimes be useful even if traceback is empty/limited.
        if not result and current_frame is not None:
            result.append(self._frame_info(current_frame, current_frame.f_lineno))

        return result

    def _frame_info(self, frame: FrameType, lineno: int) -> dict[str, Any]:
        filename = frame.f_code.co_filename
        function = frame.f_code.co_name

        lines = linecache.getlines(filename)
        start = max(1, lineno - self.config.context_radius)
        end = min(len(lines), lineno + self.config.context_radius)

        context = [lines[i - 1].rstrip("\n") for i in range(start, end + 1)] if lines else []
        used_locals = self._locals_used_in_context(frame, context)

        info = {
            "file": filename,
            "line_number": lineno,
            "function": function,
            "code_context_start_line": start,
            "code_context": context,
            "locals_in_context": used_locals,
            "all_locals": {},
            "all_globals": {},
        }

        try:
            info["all_locals"] = {
                k: _safe_repr(v, self.config.repr_max_len)
                for k, v in frame.f_locals.items()
            }
        except Exception:
            pass

        if self.config.capture_globals:
            try:
                info["all_globals"] = {
                    k: _safe_repr(v, self.config.repr_max_len)
                    for k, v in frame.f_globals.items()
                }
            except Exception:
                pass

        return info

    def _locals_used_in_context(self, frame: FrameType, context_lines: list[str]) -> dict[str, str]:
        found_names: set[str] = set()

        for line in context_lines:
            for name in frame.f_locals.keys():
                if re.search(rf"\b{re.escape(name)}\b", line):
                    found_names.add(name)

        return {
            name: _safe_repr(frame.f_locals[name], self.config.repr_max_len)
            for name in found_names
            if name in frame.f_locals
        }

    def _print_console_report(self, crash_data: dict[str, Any]) -> None:
        kind = crash_data["kind"]
        system_info = crash_data["system_info"]

        header = f"{kind} exception detected!!"
        color = YELLOW if kind == "handled" else RED

        print(f"{BOLD}{color}{header}{RESET}")
        print(f"{YELLOW}type: {crash_data['exception_type']}{RESET}")
        print(f"{YELLOW}message: {crash_data['exception_message']}{RESET}")
        print(f"{WHITE}source: {crash_data['source']}{RESET}")
        print(f"{WHITE}timestamp: {system_info['timestamp']}{RESET}")
        print(
            f"{WHITE}system: {system_info['system']} "
            f"{system_info['release']} ({system_info['machine']}){RESET}"
        )
        print(f"{WHITE}python: {system_info['python_version']} ({system_info['python_compiler']}){RESET}")
        print(f"{WHITE}cwd: {system_info['cwd']}{RESET}")
        print(f"{WHITE}thread: {system_info['thread_name']} ({system_info['thread_ident']}){RESET}")

        argv = system_info.get("argv")
        if argv is not None:
            print(f"{WHITE}argv: {argv}{RESET}")

        print(f"{YELLOW}stack trace (most recent call last):{RESET}")

        for frame_info in crash_data["frames"]:
            print(f"{WHITE}{frame_info['file']}:{frame_info['line_number']} in {frame_info['function']}{RESET}")

            start_line = frame_info["code_context_start_line"]
            for offset, line in enumerate(frame_info["code_context"]):
                current_line = start_line + offset
                marker = ">>" if current_line == frame_info["line_number"] else "  "
                color = RED if current_line == frame_info["line_number"] else WHITE
                print(f"{color}{marker} {current_line:4} | {line}{RESET}")

            if frame_info["locals_in_context"]:
                print(f"{YELLOW}  locals in context:{RESET}")
                for key, value in frame_info["locals_in_context"].items():
                    print(f"    {key} = {value}")

            print()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _should_ignore(self, exc_type: type[BaseException]) -> bool:
        return issubclass(exc_type, self.config.exclude_exception_types)

    def _should_print_console(self, handled: bool) -> bool:
        if handled:
            return self.config.handled_console_output
        return self.config.print_console

    def _should_save_json(self, handled: bool) -> bool:
        if handled:
            return self.config.handled_save_json
        return self.config.save_json

    def _handled_key(
        self,
        exc_value: BaseException,
        exc_tb: Optional[TracebackType],
        frame: FrameType,
    ) -> tuple[int, str, int]:
        if exc_tb is not None:
            return (id(exc_value), exc_tb.tb_frame.f_code.co_filename, exc_tb.tb_lineno)
        return (id(exc_value), frame.f_code.co_filename, frame.f_lineno)

    def _should_report_handled(
        self,
        frame: FrameType,
        exc_tb: Optional[TracebackType],
    ) -> bool:
        if not self.config.handled_only_user_code:
            return True

        try:
            filename = exc_tb.tb_frame.f_code.co_filename if exc_tb is not None else frame.f_code.co_filename
            resolved = str(Path(filename).resolve())
            return resolved.startswith(self._cwd)
        except Exception:
            return True


# -----------------------------------------------------------------------------
# Module-level convenience API
# -----------------------------------------------------------------------------

_default_reporter: Optional[CrashReporter] = None


def install(config: Optional[CrashReporterConfig] = None, **overrides: Any) -> CrashReporter:
    """
    Install the crash reporter globally.

    Example:
        install(capture_handled_exceptions=True, capture_globals=True)
    """
    global _default_reporter

    if config is None:
        config = CrashReporterConfig(**overrides)
    else:
        if overrides:
            values = dict(config.__dict__)
            values.update(overrides)
            config = CrashReporterConfig(**values)

    _default_reporter = CrashReporter(config)
    return _default_reporter.install()


def uninstall() -> None:
    global _default_reporter
    if _default_reporter is not None:
        _default_reporter.uninstall()
        _default_reporter = None