"""Implementation of the simulated MicroPython machine module."""

from __future__ import annotations

import queue
import threading
import time as _host_time
import types
import weakref as _weakref
from typing import Any, Callable, Dict, List, Optional, Union

from ._board import PinIRQ, SimulatedBoard
from ._constants import FLOATING, LOGIC_LOW
from ._exceptions import SimulationStopped
from ._runtime import _sim_sleep


class _MachinePin:
    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    ALT = 3
    ALT_OPEN_DRAIN = 4

    PULL_UP = 1
    PULL_DOWN = 2
    PULL_HOLD = 3

    IRQ_FALLING = 0x04
    IRQ_RISING = 0x08
    IRQ_LOW_LEVEL = 0x01
    IRQ_HIGH_LEVEL = 0x02

    board: SimulatedBoard

    def __init__(self, id: Union[int, str], mode: int = -1, pull: Optional[int] = None, *, value: Optional[int] = None, drive: Any = None, alt: Any = None):
        self.id = self._map_pin_id(id)
        self._irq: Optional[PinIRQ] = None
        if mode == -1:
            mode = self.board.pin_state(self.id).mode
        initial = FLOATING if value is None else int(bool(value))
        self.board.configure_pin(self.id, mode=mode, pull=pull, value=initial)

    @classmethod
    def _map_pin_id(cls, id: Union[int, str]) -> int:
        if id == "LED":
            led = cls.board.spec.onboard_led_pin
            if isinstance(led, int):
                return led
            # W boards put LED behind CYW43. Use a virtual shim pin at GPIO25 for
            # API compatibility while also updating board.cyw43_led in value().
            return 25
        if isinstance(id, str) and id.upper().startswith("GP"):
            return int(id[2:])
        return int(id)

    def init(self, mode: int = -1, pull: Optional[int] = None, *, value: Optional[int] = None, drive: Any = None, alt: Any = None) -> None:
        if mode == -1:
            mode = self.board.pin_state(self.id).mode
        self.board.configure_pin(self.id, mode=mode, pull=pull, value=FLOATING if value is None else int(bool(value)))

    def value(self, x: Optional[int] = None) -> int:
        self.board.check_stopped()
        if x is None:
            if self.id == 25 and isinstance(self.board.spec.onboard_led_pin, str):
                return int(self.board.cyw43_led)
            return self.board.read_pin(self.id)
        level = int(bool(x))
        st = self.board.pin_state(self.id)
        if st.mode in (self.OUT, self.ALT):
            self.board.drive_pin_cpu(self.id, level)
        elif st.mode in (self.OPEN_DRAIN, self.ALT_OPEN_DRAIN):
            self.board.drive_pin_cpu(self.id, LOGIC_LOW if level == 0 else FLOATING)
        else:
            # MicroPython permits value writes around init patterns; be tolerant.
            self.board.drive_pin_cpu(self.id, level)
        if self.id == 25 and isinstance(self.board.spec.onboard_led_pin, str):
            self.board.cyw43_led = bool(level)
        return level

    def __call__(self, x: Optional[int] = None) -> int:
        return self.value(x)

    def on(self) -> None:
        self.value(1)

    def off(self) -> None:
        self.value(0)

    def high(self) -> None:
        self.value(1)

    def low(self) -> None:
        self.value(0)

    def toggle(self) -> None:
        self.value(0 if self.value() else 1)

    def irq(self, handler: Optional[Callable[[Any], None]] = None, trigger: int = IRQ_FALLING | IRQ_RISING, *, hard: bool = False) -> PinIRQ:
        if handler is None:
            if self._irq is None:
                self._irq = PinIRQ(_weakref.ref(self), lambda pin: None, trigger, hard)
            return self._irq
        if self._irq is not None:
            self.board.remove_irq(self.id, self._irq)
        self._irq = PinIRQ(_weakref.ref(self), handler, trigger, hard)
        self.board.add_irq(self.id, self._irq)
        return self._irq

    def mode(self) -> int:
        return self.board.pin_state(self.id).mode

    def pull(self) -> Optional[int]:
        return self.board.pin_state(self.id).pull

    def __repr__(self) -> str:
        return f"Pin({self.id}, mode={self.mode()}, value={self.value()})"


class _MachineADC:
    board: SimulatedBoard

    def __init__(self, id: Union[int, _MachinePin]):
        self.id = id.id if isinstance(id, _MachinePin) else int(id)
        self._source: Optional[Callable[[], int]] = None

    def set_source(self, source: Callable[[], int]) -> None:
        self._source = source

    def read_u16(self) -> int:
        self.board.check_stopped()
        if self._source is not None:
            return max(0, min(65535, int(self._source())))
        # Internal temperature sensor channel: return about 27 C equivalent.
        if self.id == 4:
            return int((0.706 / 3.3) * 65535)
        # GPIO ADC reads high as full scale and low as zero for simple tests.
        pin = self.id if self.id in self.board.spec.adc_gpio else self.board.spec.adc_gpio[self.id] if 0 <= self.id < len(self.board.spec.adc_gpio) else None
        if pin is None:
            return 0
        return 65535 if self.board.read_pin(pin) else 0

    def read_uv(self) -> int:
        return int(self.read_u16() * 3_300_000 / 65535)


class _MachinePWM:
    def __init__(self, dest: Union[int, _MachinePin], *, freq: int = 1000, duty_u16: int = 0, duty_ns: int = 0, invert: bool = False):
        self.pin = dest if isinstance(dest, _MachinePin) else _MachinePin(dest)
        self._freq = freq
        self._duty_u16 = duty_u16
        self._duty_ns = duty_ns
        self._invert = invert
        self._active = True

    def init(self, *, freq: Optional[int] = None, duty_u16: Optional[int] = None, duty_ns: Optional[int] = None, invert: Optional[bool] = None) -> None:
        if freq is not None:
            self._freq = int(freq)
        if duty_u16 is not None:
            self.duty_u16(duty_u16)
        if duty_ns is not None:
            self.duty_ns(duty_ns)
        if invert is not None:
            self._invert = bool(invert)

    def deinit(self) -> None:
        self._active = False
        self.pin.value(0)

    def freq(self, value: Optional[int] = None) -> int:
        if value is not None:
            self._freq = int(value)
        return self._freq

    def duty_u16(self, value: Optional[int] = None) -> int:
        if value is not None:
            self._duty_u16 = max(0, min(65535, int(value)))
            self.pin.value(1 if self._duty_u16 >= 32768 else 0)
        return self._duty_u16

    def duty_ns(self, value: Optional[int] = None) -> int:
        if value is not None:
            self._duty_ns = max(0, int(value))
        return self._duty_ns


class _MachineTimer:
    ONE_SHOT = 0
    PERIODIC = 1
    board: SimulatedBoard

    def __init__(self, id: int = -1):
        self.id = id
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._callback: Optional[Callable[[Any], None]] = None
        self._period_s = 0.0
        self._mode = self.PERIODIC
        self.board._timers.append(self)

    def init(self, *, mode: int = PERIODIC, freq: int = -1, period: int = -1, callback: Optional[Callable[[Any], None]] = None) -> None:
        self.deinit(join=False)
        self._stop.clear()
        self._mode = mode
        self._callback = callback
        if freq and freq > 0:
            self._period_s = 1.0 / float(freq)
        elif period and period > 0:
            self._period_s = float(period) / 1000.0
        else:
            raise ValueError("Timer.init requires freq or period")
        self._thread = threading.Thread(target=self._run, name=f"picosim-timer-{self.id}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._period_s):
            if self.board.stop_event.is_set():
                break
            if self._callback:
                self._callback(self)
            if self._mode == self.ONE_SHOT:
                break

    def deinit(self, join: bool = True) -> None:
        self._stop.set()
        if join and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.2)
        self._thread = None


class _MachineI2C:
    board: SimulatedBoard
    DEFAULTS = {
        0: {"scl": 9, "sda": 8, "freq": 400_000},
        1: {"scl": 7, "sda": 6, "freq": 400_000},
    }

    def __init__(self, id: int = 0, *, scl: Optional[_MachinePin] = None, sda: Optional[_MachinePin] = None, freq: int = 400_000, timeout: int = 50_000):
        self.id = int(id)
        d = self.DEFAULTS.get(self.id, self.DEFAULTS[0])
        self.scl = scl or _MachinePin(d["scl"])
        self.sda = sda or _MachinePin(d["sda"])
        self.freq = int(freq or d["freq"])
        self.timeout = timeout

    def init(self, *, scl: Optional[_MachinePin] = None, sda: Optional[_MachinePin] = None, freq: Optional[int] = None) -> None:
        if scl is not None:
            self.scl = scl
        if sda is not None:
            self.sda = sda
        if freq is not None:
            self.freq = int(freq)

    def scan(self) -> List[int]:
        return self.board.i2c_scan()

    def writeto(self, addr: int, buf: Union[bytes, bytearray, memoryview], stop: bool = True) -> int:
        return self.board.i2c_write(addr, bytes(buf))

    def readfrom(self, addr: int, nbytes: int, stop: bool = True) -> bytes:
        return self.board.i2c_read(addr, nbytes)

    def writeto_mem(self, addr: int, memaddr: int, buf: Union[bytes, bytearray, memoryview], *, addrsize: int = 8) -> int:
        return self.board.i2c_write(addr, bytes(buf), memaddr)

    def readfrom_mem(self, addr: int, memaddr: int, nbytes: int, *, addrsize: int = 8) -> bytes:
        return self.board.i2c_read(addr, nbytes, memaddr)

    def readfrom_into(self, addr: int, buf: bytearray, stop: bool = True) -> None:
        data = self.readfrom(addr, len(buf), stop)
        buf[:len(data)] = data

    def writeto_mem_into(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("MicroPython I2C writeto_mem_into is not a standard method")

    def __repr__(self) -> str:
        return f"I2C({self.id}, scl=Pin({self.scl.id}), sda=Pin({self.sda.id}), freq={self.freq})"


class _MachineSoftI2C(_MachineI2C):
    pass


class _MachineSPI:
    board: SimulatedBoard
    DEFAULTS = {
        0: {"sck": 6, "mosi": 7, "miso": 4, "baudrate": 1_000_000},
        1: {"sck": 10, "mosi": 11, "miso": 8, "baudrate": 1_000_000},
    }
    MSB = 0
    LSB = 1

    def __init__(self, id: int = 0, baudrate: int = 1_000_000, *, polarity: int = 0, phase: int = 0, bits: int = 8, firstbit: int = MSB, sck: Optional[_MachinePin] = None, mosi: Optional[_MachinePin] = None, miso: Optional[_MachinePin] = None):
        self.id = int(id)
        d = self.DEFAULTS.get(self.id, self.DEFAULTS[0])
        self.baudrate = int(baudrate or d["baudrate"])
        self.polarity = polarity
        self.phase = phase
        self.bits = bits
        self.firstbit = firstbit
        self.sck = sck or _MachinePin(d["sck"])
        self.mosi = mosi or _MachinePin(d["mosi"])
        self.miso = miso or _MachinePin(d["miso"])

    def init(self, baudrate: Optional[int] = None, *, polarity: Optional[int] = None, phase: Optional[int] = None, bits: Optional[int] = None, firstbit: Optional[int] = None, sck: Optional[_MachinePin] = None, mosi: Optional[_MachinePin] = None, miso: Optional[_MachinePin] = None) -> None:
        if baudrate is not None:
            self.baudrate = int(baudrate)
        if polarity is not None:
            self.polarity = polarity
        if phase is not None:
            self.phase = phase
        if bits is not None:
            self.bits = bits
        if firstbit is not None:
            self.firstbit = firstbit
        if sck is not None:
            self.sck = sck
        if mosi is not None:
            self.mosi = mosi
        if miso is not None:
            self.miso = miso

    def deinit(self) -> None:
        pass

    def write(self, buf: Union[str, bytes, bytearray, memoryview]) -> int:
        data = buf.encode() if isinstance(buf, str) else bytes(buf)
        self.board.spi_transfer(self.id, data)
        return len(data)

    def read(self, nbytes: int, write: int = 0x00) -> bytes:
        return self.board.spi_transfer(self.id, bytes([write & 0xFF] * nbytes))

    def readinto(self, buf: bytearray, write: int = 0x00) -> None:
        data = self.read(len(buf), write)
        buf[:len(data)] = data

    def write_readinto(self, write_buf: Union[str, bytes, bytearray, memoryview], read_buf: bytearray) -> None:
        data = write_buf.encode() if isinstance(write_buf, str) else bytes(write_buf)
        rx = self.board.spi_transfer(self.id, data)
        read_buf[:len(read_buf)] = rx[:len(read_buf)].ljust(len(read_buf), b"\x00")


class _MachineSoftSPI(_MachineSPI):
    pass


class _MachineUART:
    board: SimulatedBoard
    DEFAULTS = {
        0: {"tx": 0, "rx": 1, "baudrate": 115_200},
        1: {"tx": 4, "rx": 5, "baudrate": 115_200},
    }

    def __init__(self, id: int, baudrate: int = 115_200, *, bits: int = 8, parity: Optional[int] = None, stop: int = 1, tx: Optional[_MachinePin] = None, rx: Optional[_MachinePin] = None, timeout: int = 0, timeout_char: int = 0, invert: int = 0, flow: int = 0):
        self.id = int(id)
        d = self.DEFAULTS.get(self.id, self.DEFAULTS[0])
        self.baudrate = int(baudrate or d["baudrate"])
        self.bits = bits
        self.parity = parity
        self.stop = stop
        self.tx = tx or _MachinePin(d["tx"])
        self.rx = rx or _MachinePin(d["rx"])
        self.timeout = timeout
        self.timeout_char = timeout_char
        self.invert = invert
        self.flow = flow

    def init(self, baudrate: Optional[int] = None, **kwargs: Any) -> None:
        if baudrate is not None:
            self.baudrate = int(baudrate)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def any(self) -> int:
        return self.board.uart_any(self.id)

    def read(self, nbytes: Optional[int] = None) -> Optional[bytes]:
        data = self.board.uart_read(self.id, nbytes)
        return data if data else None

    def readinto(self, buf: bytearray, nbytes: Optional[int] = None) -> Optional[int]:
        n = len(buf) if nbytes is None else min(len(buf), int(nbytes))
        data = self.board.uart_read(self.id, n)
        buf[:len(data)] = data
        return len(data) if data else None

    def readline(self) -> Optional[bytes]:
        out = bytearray()
        while self.any():
            b = self.board.uart_read(self.id, 1)
            out += b
            if b in (b"\n", b"\r"):
                break
        return bytes(out) if out else None

    def write(self, buf: Union[str, bytes, bytearray, memoryview]) -> int:
        data = buf.encode() if isinstance(buf, str) else bytes(buf)
        return self.board.uart_write(self.id, data)

    def sendbreak(self) -> None:
        self.board.uart_write(self.id, b"\x00")

    def deinit(self) -> None:
        pass


class _MachineWDT:
    def __init__(self, id: int = 0, timeout: int = 5000):
        self.id = id
        self.timeout = timeout
        self.last_feed = _host_time.monotonic()

    def feed(self) -> None:
        self.last_feed = _host_time.monotonic()


class _MachineRTC:
    def __init__(self):
        self._datetime: Optional[tuple[int, int, int, int, int, int, int, int]] = None

    def datetime(self, value: Optional[tuple[int, int, int, int, int, int, int, int]] = None):
        if value is not None:
            self._datetime = value
        if self._datetime is not None:
            return self._datetime
        t = _host_time.localtime()
        return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_wday, t.tm_hour, t.tm_min, t.tm_sec, 0)


def _make_machine_module(board: SimulatedBoard) -> types.ModuleType:
    mod = types.ModuleType("machine")
    for cls in (_MachinePin, _MachineADC, _MachineTimer, _MachineI2C, _MachineSoftI2C, _MachineSPI, _MachineSoftSPI, _MachineUART):
        cls.board = board
    _MachineWDT.board = board  # type: ignore[attr-defined]
    mod.Pin = _MachinePin
    mod.ADC = _MachineADC
    mod.PWM = _MachinePWM
    mod.Timer = _MachineTimer
    mod.I2C = _MachineI2C
    mod.SoftI2C = _MachineSoftI2C
    mod.SPI = _MachineSPI
    mod.SoftSPI = _MachineSoftSPI
    mod.UART = _MachineUART
    mod.WDT = _MachineWDT
    mod.RTC = _MachineRTC
    mod.freq = lambda hz=None: _machine_freq(board, hz)
    mod.reset = lambda: (_ for _ in ()).throw(SimulationStopped())
    mod.soft_reset = mod.reset
    mod.unique_id = lambda: b"PICOSIM"
    mod.idle = lambda: _host_time.sleep(0)
    mod.disable_irq = lambda: 0
    mod.enable_irq = lambda state=0: None
    mod.lightsleep = lambda ms=None: _sim_sleep(board, ms, 1000)
    mod.deepsleep = lambda ms=None: _sim_sleep(board, ms, 1000)
    return mod


def _machine_freq(board: SimulatedBoard, hz: Optional[int] = None) -> int:
    if hz is not None:
        board.clock_hz = int(hz)
    return board.clock_hz


__all__ = [
    "_MachinePin",
    "_MachineADC",
    "_MachinePWM",
    "_MachineTimer",
    "_MachineI2C",
    "_MachineSoftI2C",
    "_MachineSPI",
    "_MachineSoftSPI",
    "_MachineUART",
    "_MachineWDT",
    "_MachineRTC",
    "_make_machine_module",
    "_machine_freq",
]
