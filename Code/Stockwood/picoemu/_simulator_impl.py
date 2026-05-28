"""High-level simulator runner and result types."""

from __future__ import annotations

import importlib
import os as _os
import sys as _sys
import threading
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Union

from ._board import SimulatedBoard
from ._constants import BoardModel, BoardSpec
from ._exceptions import SimulationStopped
from ._machine import _make_machine_module
from ._network import _make_network_module
from ._overlay import _ModuleOverlay
from ._rp2 import _make_rp2_module
from ._runtime import _sim_sleep
from ._timecompat import _make_micropython_module, _make_time_module


@dataclass
class RunResult:
    globals: Dict[str, Any] = field(default_factory=dict)
    exception: Optional[BaseException] = None
    traceback: Optional[str] = None
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.exception is None or isinstance(self.exception, SimulationStopped)

    def raise_if_failed(self) -> None:
        if self.exception is not None and not isinstance(self.exception, SimulationStopped):
            raise self.exception


class ScriptRun:
    """Handle returned by run_script_threaded()."""

    def __init__(self, thread: threading.Thread, result: RunResult, simulator: "PicoSimulator"):
        self.thread = thread
        self.result = result
        self.simulator = simulator

    def join(self, timeout: Optional[float] = None) -> RunResult:
        self.thread.join(timeout)
        return self.result

    def stop(self) -> None:
        self.simulator.stop()

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


class PicoSimulator:
    """
    Main controller-facing API.

    Use install_context() to run your own code inside the simulated module world,
    or run_script()/run_script_threaded() to execute a target script.
    """

    def __init__(self, model: Union[BoardModel, str, BoardSpec] = BoardModel.PICO, *, restrict_imports: bool = True, extra_allowed_modules: Iterable[str] = ()): 
        if isinstance(model, str):
            model = BoardModel(model)
        self.board = SimulatedBoard(model)
        self.restrict_imports = restrict_imports
        self.extra_allowed_modules = set(extra_allowed_modules)
        self._modules = self._build_modules()

    def _build_modules(self) -> Dict[str, types.ModuleType]:
        time_mod = _make_time_module(self.board, "time")
        utime_mod = _make_time_module(self.board, "utime")
        modules: Dict[str, types.ModuleType] = {
            "machine": _make_machine_module(self.board),
            "micropython": _make_micropython_module(),
            "rp2": _make_rp2_module(self.board),
            "network": _make_network_module(self.board),
            "time": time_mod,
            "utime": utime_mod,
            "uasyncio": importlib.import_module("asyncio"),
            "ubinascii": importlib.import_module("binascii"),
            "ujson": importlib.import_module("json"),
            "uos": importlib.import_module("os"),
            "ustruct": importlib.import_module("struct"),
        }
        return modules

    def install_context(self):
        return _ModuleOverlay(self._modules, self.restrict_imports, self.extra_allowed_modules)

    def sleep(self, seconds: float) -> None:
        _sim_sleep(self.board, seconds, 1)

    def stop(self) -> None:
        self.board.stop()

    def reset(self) -> None:
        self.board.reset()

    def run_script(self, filename: Union[str, Path], *, argv: Optional[Sequence[str]] = None, cwd: Optional[Union[str, Path]] = None, globals_init: Optional[Dict[str, Any]] = None) -> RunResult:
        result = RunResult()
        old_argv = list(_sys.argv)
        old_cwd = _os.getcwd()
        filename = Path(filename)
        if argv is None:
            argv = [str(filename)]
        try:
            _sys.argv = list(argv)
            if cwd is not None:
                _os.chdir(cwd)
            with self.install_context():
                g = {"__name__": "__main__", "__file__": str(filename)}
                if globals_init:
                    g.update(globals_init)
                code = filename.read_text(encoding="utf-8")
                exec(compile(code, str(filename), "exec"), g, g)
                result.globals = g
        except SimulationStopped as exc:
            result.exception = exc
            result.stopped = True
        except BaseException as exc:
            result.exception = exc
            result.traceback = traceback.format_exc()
        finally:
            _sys.argv = old_argv
            _os.chdir(old_cwd)
        return result

    def run_code(self, code: str, *, filename: str = "<picosim>", globals_init: Optional[Dict[str, Any]] = None) -> RunResult:
        tmp_globals = {"__name__": "__main__", "__file__": filename}
        if globals_init:
            tmp_globals.update(globals_init)
        result = RunResult(globals=tmp_globals)
        try:
            with self.install_context():
                exec(compile(code, filename, "exec"), tmp_globals, tmp_globals)
        except SimulationStopped as exc:
            result.exception = exc
            result.stopped = True
        except BaseException as exc:
            result.exception = exc
            result.traceback = traceback.format_exc()
        return result

    def run_script_threaded(self, filename: Union[str, Path], **kwargs: Any) -> ScriptRun:
        result = RunResult()

        def runner() -> None:
            r = self.run_script(filename, **kwargs)
            result.globals = r.globals
            result.exception = r.exception
            result.traceback = r.traceback
            result.stopped = r.stopped

        thread = threading.Thread(target=runner, name=f"picosim-script-{Path(filename).name}", daemon=True)
        thread.start()
        return ScriptRun(thread, result, self)

    def run_code_threaded(self, code: str, **kwargs: Any) -> ScriptRun:
        result = RunResult()

        def runner() -> None:
            r = self.run_code(code, **kwargs)
            result.globals = r.globals
            result.exception = r.exception
            result.traceback = r.traceback
            result.stopped = r.stopped

        thread = threading.Thread(target=runner, name="picosim-code", daemon=True)
        thread.start()
        return ScriptRun(thread, result, self)


__all__ = ["RunResult", "ScriptRun", "PicoSimulator"]
