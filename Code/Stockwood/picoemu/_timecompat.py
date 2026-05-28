"""time/utime and micropython compatibility modules."""

from __future__ import annotations

import importlib
import types

from ._board import SimulatedBoard
from ._runtime import _sim_sleep


def _make_time_module(board: SimulatedBoard, name: str = "time") -> types.ModuleType:
    host = importlib.import_module("time")
    mod = types.ModuleType(name)
    for attr in dir(host):
        if not attr.startswith("__"):
            setattr(mod, attr, getattr(host, attr))
    mod.sleep = lambda s=0: _sim_sleep(board, s, 1)
    mod.sleep_ms = lambda ms=0: _sim_sleep(board, ms, 1000)
    mod.sleep_us = lambda us=0: _sim_sleep(board, us, 1_000_000)
    mod.ticks_ms = board.ticks_ms
    mod.ticks_us = board.ticks_us
    mod.ticks_cpu = board.ticks_cpu
    mod.ticks_add = lambda ticks, delta: (int(ticks) + int(delta)) & 0x3FFFFFFF
    mod.ticks_diff = lambda a, b: ((int(a) - int(b) + 0x20000000) & 0x3FFFFFFF) - 0x20000000
    return mod


def _make_micropython_module() -> types.ModuleType:
    mod = types.ModuleType("micropython")
    mod.const = lambda x: x
    mod.alloc_emergency_exception_buf = lambda size: None
    mod.mem_info = lambda *a, **k: print("picosim: mem_info unavailable on CPython")
    mod.qstr_info = lambda *a, **k: print("picosim: qstr_info unavailable on CPython")
    mod.schedule = lambda func, arg: func(arg)
    mod.opt_level = lambda level=None: 0
    return mod


__all__ = ["_make_time_module", "_make_micropython_module"]
