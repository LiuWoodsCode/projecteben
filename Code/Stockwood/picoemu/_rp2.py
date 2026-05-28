"""rp2 module stubs with basic PIO simulation helpers."""

from __future__ import annotations

import queue
import types
from typing import Any, Callable, Mapping, Optional

from ._board import SimulatedBoard
from ._machine import _MachinePin


class _PIOProgram:
    def __init__(self, func: Callable[..., Any], config: Mapping[str, Any]):
        self.func = func
        self.config = dict(config)
        self.name = getattr(func, "__name__", "pio_program")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<PIOProgram {self.name} stub>"


class _PIO:
    IN_LOW = 0
    IN_HIGH = 1
    OUT_LOW = 2
    OUT_HIGH = 3
    SHIFT_LEFT = 0
    SHIFT_RIGHT = 1
    JOIN_NONE = 0
    JOIN_TX = 1
    JOIN_RX = 2

    def __init__(self, id: int = 0):
        self.id = id
        self._irq_handler: Optional[Callable[[Any], None]] = None
        self._flags = 0

    def irq(self, handler: Optional[Callable[[Any], None]] = None, trigger: Any = None, hard: bool = False):
        if handler is not None:
            self._irq_handler = handler
        return self

    def flags(self) -> int:
        return self._flags

    def remove_program(self, program: Any = None) -> None:
        pass


class _StateMachine:
    board: SimulatedBoard

    def __init__(self, id: int, program: Any, freq: int = 125_000_000, **kwargs: Any):
        self.id = int(id)
        self.program = program
        self.freq = int(freq)
        self.kwargs = kwargs
        self._active = False
        self._tx_fifo: queue.Queue[int] = queue.Queue()
        self._rx_fifo: queue.Queue[int] = queue.Queue()
        self._irq_handler: Optional[Callable[[Any], None]] = None

    def active(self, value: Optional[int] = None) -> int:
        if value is not None:
            self._active = bool(value)
        return int(self._active)

    def restart(self) -> None:
        while not self._tx_fifo.empty():
            self._tx_fifo.get_nowait()
        while not self._rx_fifo.empty():
            self._rx_fifo.get_nowait()

    def exec(self, instr: str) -> None:
        # Deliberately tiny stub: enough for simple set(pins, N) tests.
        text = instr.strip().replace(" ", "")
        if text.startswith("set(pins,") and text.endswith(")"):
            val = int(text[len("set(pins,"):-1], 0)
            pin = self.kwargs.get("set_base") or self.kwargs.get("sideset_base") or self.kwargs.get("out_base")
            if isinstance(pin, _MachinePin):
                pin.value(val & 1)

    def put(self, value: Any, shift: int = 0) -> None:
        if isinstance(value, (bytes, bytearray)):
            for b in value:
                self._tx_fifo.put(int(b) << shift)
        elif hasattr(value, "__iter__") and not isinstance(value, (str, int)):
            for item in value:
                self._tx_fifo.put(int(item) << shift)
        else:
            self._tx_fifo.put(int(value) << shift)

    def get(self, buf: Any = None, shift: int = 0) -> int:
        try:
            value = self._rx_fifo.get_nowait()
        except queue.Empty:
            try:
                value = self._tx_fifo.get_nowait()
            except queue.Empty:
                value = 0
        return int(value) >> shift

    def rx_fifo(self) -> int:
        return self._rx_fifo.qsize()

    def tx_fifo(self) -> int:
        return self._tx_fifo.qsize()

    def irq(self, handler: Optional[Callable[[Any], None]] = None, trigger: Any = None, hard: bool = False):
        if handler is not None:
            self._irq_handler = handler
        return self

    def __repr__(self) -> str:
        return f"StateMachine({self.id}, active={self._active}, freq={self.freq})"


def _asm_pio(**config: Any):
    def decorator(func: Callable[..., Any]) -> _PIOProgram:
        return _PIOProgram(func, config)

    return decorator


def _asm_pio_encode(instr: str, sideset_count: int = 0, sideset_opt: bool = False) -> int:
    # Placeholder. Real PIO encoding is intentionally out of scope.
    return hash((instr, sideset_count, sideset_opt)) & 0xFFFF


def _make_rp2_module(board: SimulatedBoard) -> types.ModuleType:
    mod = types.ModuleType("rp2")
    _StateMachine.board = board
    mod.PIO = _PIO
    mod.StateMachine = _StateMachine
    mod.asm_pio = _asm_pio
    mod.asm_pio_encode = _asm_pio_encode
    mod.bootsel_button = lambda: 0
    mod.Flash = lambda: (_ for _ in ()).throw(NotImplementedError("raw flash access is not simulated"))
    return mod


__all__ = [
    "_PIOProgram",
    "_PIO",
    "_StateMachine",
    "_asm_pio",
    "_asm_pio_encode",
    "_make_rp2_module",
]
