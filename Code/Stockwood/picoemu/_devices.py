"""Pin-level and bus-level software devices for picoemu."""

from __future__ import annotations

import queue
import time as _host_time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, Union

from ._constants import FLOATING, LOGIC_LOW, PinLevel
from ._exceptions import PicoSimError

if TYPE_CHECKING:
    from ._board import PinIRQ, SimulatedBoard


@dataclass
class PinState:
    number: int
    mode: int = 0
    pull: Optional[int] = None
    alt: Optional[str] = None
    cpu_drive: PinLevel = FLOATING
    external_drives: Dict[str, PinLevel] = field(default_factory=dict)
    last_level: int = LOGIC_LOW
    irq_handlers: List["PinIRQ"] = field(default_factory=list)


class PinDevice:
    """
    Base class for a pin-level software device.

    A device may observe pin changes and may drive one or more pins. Drive with
    None to leave the pin high-Z/floating. Device drive is intentionally explicit:
    the Board owns the final logic resolution and IRQ dispatch.
    """

    def __init__(self, name: str):
        self.name = name
        self.board: Optional[SimulatedBoard] = None
        self.pins: Tuple[int, ...] = ()

    def attach(self, board: "SimulatedBoard", pins: Sequence[int]) -> None:
        self.board = board
        self.pins = tuple(int(p) for p in pins)
        self.on_attach()

    def detach(self) -> None:
        if self.board is not None:
            for pin in self.pins:
                self.board.drive_pin_external(self.name, pin, FLOATING)
        self.on_detach()
        self.board = None
        self.pins = ()

    def on_attach(self) -> None:
        pass

    def on_detach(self) -> None:
        pass

    def on_pin_changed(self, pin: int, old: int, new: int) -> None:
        pass

    def drive(self, pin: int, level: PinLevel) -> None:
        if self.board is None:
            raise PicoSimError(f"device {self.name!r} is not attached")
        self.board.drive_pin_external(self.name, pin, level)

    def read(self, pin: int) -> int:
        if self.board is None:
            raise PicoSimError(f"device {self.name!r} is not attached")
        return self.board.read_pin(pin)


class LedDevice(PinDevice):
    """A simple LED-like input device. It observes a pin and records on/off state."""

    def __init__(self, name: str = "led", active_high: bool = True):
        super().__init__(name)
        self.active_high = active_high
        self.is_on = False
        self.history: List[Tuple[float, bool]] = []

    def on_attach(self) -> None:
        if not self.pins:
            return
        self._sample(self.pins[0])

    def on_pin_changed(self, pin: int, old: int, new: int) -> None:
        if self.pins and pin == self.pins[0]:
            self._sample(pin)

    def _sample(self, pin: int) -> None:
        level = self.read(pin)
        state = bool(level) if self.active_high else not bool(level)
        if state != self.is_on:
            self.is_on = state
            self.history.append((_host_time.monotonic(), state))


class ButtonDevice(PinDevice):
    """A push button. By default it pulls the pin low while pressed."""

    def __init__(self, name: str = "button", pressed_level: int = LOGIC_LOW):
        super().__init__(name)
        self.pressed_level = 1 if pressed_level else 0
        self.pressed = False

    def on_attach(self) -> None:
        self.release()

    def press(self) -> None:
        self.pressed = True
        if self.pins:
            self.drive(self.pins[0], self.pressed_level)

    def release(self) -> None:
        self.pressed = False
        if self.pins and self.board is not None:
            self.drive(self.pins[0], FLOATING)


class WireDevice(PinDevice):
    """Tie two or more GPIO pins together at a logic level, useful for UART loopback."""

    def __init__(self, name: str = "wire"):
        super().__init__(name)
        self._updating = False

    def on_pin_changed(self, pin: int, old: int, new: int) -> None:
        if self._updating:
            return
        if pin not in self.pins:
            return
        self._updating = True
        try:
            for other in self.pins:
                if other != pin:
                    self.drive(other, new)
        finally:
            self._updating = False


class I2CDevice(PinDevice):
    """Base for bus-level I2C devices used by the simulated machine.I2C."""

    address: int

    def i2c_read(self, nbytes: int, memaddr: Optional[int] = None) -> bytes:
        return bytes([0xFF] * nbytes)

    def i2c_write(self, data: bytes, memaddr: Optional[int] = None) -> int:
        return len(data)


class I2CMemoryDevice(I2CDevice):
    """Tiny I2C EEPROM/register-map style device for tests."""

    def __init__(self, address: int, size: int = 256, name: Optional[str] = None):
        super().__init__(name or f"i2c_mem_{address:02x}")
        self.address = address & 0x7F
        self.memory = bytearray(size)
        self.pointer = 0

    def i2c_write(self, data: bytes, memaddr: Optional[int] = None) -> int:
        if memaddr is None:
            if not data:
                return 0
            self.pointer = data[0] % len(self.memory)
            payload = data[1:]
        else:
            self.pointer = memaddr % len(self.memory)
            payload = data
        for b in payload:
            self.memory[self.pointer] = b
            self.pointer = (self.pointer + 1) % len(self.memory)
        return len(data)

    def i2c_read(self, nbytes: int, memaddr: Optional[int] = None) -> bytes:
        if memaddr is not None:
            self.pointer = memaddr % len(self.memory)
        out = bytearray()
        for _ in range(nbytes):
            out.append(self.memory[self.pointer])
            self.pointer = (self.pointer + 1) % len(self.memory)
        return bytes(out)


class SPIDevice(PinDevice):
    """Base for bus-level SPI devices used by the simulated machine.SPI."""

    def spi_transfer(self, data: bytes) -> bytes:
        return bytes([0xFF] * len(data))


class LoopbackSPIDevice(SPIDevice):
    """SPI device that echoes transferred bytes."""

    def spi_transfer(self, data: bytes) -> bytes:
        return bytes(data)


class UARTEndpoint(PinDevice):
    """Simple external UART endpoint. It observes/writes via the simulated UART bus."""

    def __init__(self, uart_id: int, name: Optional[str] = None):
        super().__init__(name or f"uart_endpoint_{uart_id}")
        self.uart_id = uart_id
        self.rx = queue.Queue()  # bytes received from Pico UART writes

    def write_to_pico(self, data: Union[str, bytes, bytearray]) -> None:
        if self.board is None:
            raise PicoSimError("UART endpoint is not attached")
        b = data.encode() if isinstance(data, str) else bytes(data)
        self.board.uart_inject_rx(self.uart_id, b)

    def read_from_pico(self, n: Optional[int] = None) -> bytes:
        chunks: List[int] = []
        limit = n if n is not None else self.rx.qsize()
        for _ in range(limit):
            try:
                chunks.append(self.rx.get_nowait())
            except queue.Empty:
                break
        return bytes(chunks)

    def _receive_from_uart(self, data: bytes) -> None:
        for b in data:
            self.rx.put(b)


__all__ = [
    "PinState",
    "PinDevice",
    "LedDevice",
    "ButtonDevice",
    "WireDevice",
    "I2CDevice",
    "I2CMemoryDevice",
    "SPIDevice",
    "LoopbackSPIDevice",
    "UARTEndpoint",
]
