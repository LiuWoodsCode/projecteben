"""Board-level state, GPIO resolution, and peripheral buses for picoemu."""

from __future__ import annotations

import contextlib
import errno as _errno
import queue
import threading
import time as _host_time
import weakref as _weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union

from ._constants import BOARD_SPECS, BoardModel, BoardSpec, FLOATING, LOGIC_HIGH, LOGIC_LOW
from ._devices import I2CDevice, PinDevice, PinState, SPIDevice, UARTEndpoint
from ._exceptions import PicoSimError, PinContentionError, SimulationStopped

_PULL_UP = 1
_PULL_DOWN = 2
_IRQ_FALLING = 0x04
_IRQ_RISING = 0x08


@dataclass
class PinIRQ:
    pin_obj_ref: _weakref.ReferenceType
    handler: Callable[[Any], None]
    trigger: int
    hard: bool = False
    last_flags: int = 0

    def fire(self, flag: int) -> None:
        self.last_flags = flag
        pin_obj = self.pin_obj_ref()
        if pin_obj is not None:
            self.handler(pin_obj)

    def flags(self) -> int:
        return self.last_flags


class SimulatedBoard:
    """Board state and peripheral buses visible to the simulator/controller."""

    def __init__(self, spec: Union[BoardModel, BoardSpec] = BoardModel.PICO):
        if isinstance(spec, BoardModel):
            spec = BOARD_SPECS[spec]
        self.spec = spec
        self.clock_hz = spec.default_sys_clock_hz
        self._pins: Dict[int, PinState] = {i: PinState(i) for i in range(spec.gpio_count)}
        self._devices: Dict[str, PinDevice] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._timers: List[Any] = []
        self._i2c_devices: Dict[int, I2CDevice] = {}
        self._spi_devices: Dict[int, SPIDevice] = {}
        self._uart_rx: Dict[int, queue.Queue[int]] = {0: queue.Queue(), 1: queue.Queue()}
        self._uart_endpoints: Dict[int, List[UARTEndpoint]] = {0: [], 1: []}
        self._start_monotonic = _host_time.monotonic()
        self.cyw43_led = False
        self.network_connected = False
        self.network_config = ("0.0.0.0", "255.255.255.0", "0.0.0.0", "0.0.0.0")

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def reset(self) -> None:
        with self._lock:
            self.clock_hz = self.spec.default_sys_clock_hz
            for st in self._pins.values():
                st.mode = 0
                st.pull = None
                st.alt = None
                st.cpu_drive = FLOATING
                st.last_level = self._resolve_pin_locked(st.number)
                st.irq_handlers.clear()
            self.cyw43_led = False
            self._start_monotonic = _host_time.monotonic()
            self._stop_event.clear()

    def stop(self) -> None:
        self._stop_event.set()
        for timer in list(self._timers):
            timer.deinit()

    def check_stopped(self) -> None:
        if self._stop_event.is_set():
            raise SimulationStopped()

    def attach_device(self, device: PinDevice, *pins: int) -> PinDevice:
        with self._lock:
            if device.name in self._devices:
                raise PicoSimError(f"device name already attached: {device.name}")
            for pin in pins:
                self._validate_pin(pin)
            self._devices[device.name] = device
            device.attach(self, pins)
            if isinstance(device, I2CDevice):
                self._i2c_devices[device.address] = device
            if isinstance(device, SPIDevice):
                # SPI bus assignment can be overridden by controller if needed.
                self._spi_devices.setdefault(0, device)
            if isinstance(device, UARTEndpoint):
                self._uart_endpoints.setdefault(device.uart_id, []).append(device)
            return device

    def detach_device(self, name_or_device: Union[str, PinDevice]) -> None:
        name = name_or_device if isinstance(name_or_device, str) else name_or_device.name
        with self._lock:
            dev = self._devices.pop(name)
            if isinstance(dev, I2CDevice):
                self._i2c_devices.pop(dev.address, None)
            if isinstance(dev, UARTEndpoint):
                with contextlib.suppress(ValueError):
                    self._uart_endpoints.get(dev.uart_id, []).remove(dev)
            dev.detach()

    def set_spi_device(self, bus_id: int, device: SPIDevice) -> None:
        self._spi_devices[bus_id] = device

    def pin_state(self, pin: int) -> PinState:
        self._validate_pin(pin)
        return self._pins[pin]

    def read_pin(self, pin: int) -> int:
        with self._lock:
            self._validate_pin(pin)
            return self._resolve_pin_locked(pin)

    def configure_pin(self, pin: int, mode: Optional[int] = None, pull: Optional[int] = None, value: Optional[int] = FLOATING) -> None:
        with self._lock:
            self._validate_pin(pin)
            st = self._pins[pin]
            if mode is not None:
                st.mode = mode
            st.pull = pull
            if value is not FLOATING:
                st.cpu_drive = 1 if value else 0
            elif mode is not None and mode != 1:
                st.cpu_drive = FLOATING
            self._propagate_pin_change_locked(pin)

    def drive_pin_cpu(self, pin: int, level: Optional[int]) -> None:
        with self._lock:
            self._validate_pin(pin)
            self._pins[pin].cpu_drive = FLOATING if level is FLOATING else (1 if level else 0)
            self._propagate_pin_change_locked(pin)

    def drive_pin_external(self, device_name: str, pin: int, level: Optional[int]) -> None:
        with self._lock:
            self._validate_pin(pin)
            self._pins[pin].external_drives[device_name] = FLOATING if level is FLOATING else (1 if level else 0)
            self._propagate_pin_change_locked(pin)

    def add_irq(self, pin: int, irq: PinIRQ) -> None:
        with self._lock:
            self._validate_pin(pin)
            self._pins[pin].irq_handlers.append(irq)

    def remove_irq(self, pin: int, irq: PinIRQ) -> None:
        with self._lock:
            with contextlib.suppress(ValueError):
                self._pins[pin].irq_handlers.remove(irq)

    def _resolve_pin_locked(self, pin: int) -> int:
        st = self._pins[pin]
        driven = []
        if st.cpu_drive is not FLOATING:
            driven.append(st.cpu_drive)
        driven.extend(v for v in st.external_drives.values() if v is not FLOATING)
        if driven:
            if any(v != driven[0] for v in driven):
                raise PinContentionError(f"conflicting drive levels on GPIO{pin}: {driven}")
            return int(driven[0])
        if st.pull == _PULL_UP:
            return LOGIC_HIGH
        if st.pull == _PULL_DOWN:
            return LOGIC_LOW
        return st.last_level if st.last_level in (0, 1) else LOGIC_LOW

    def _propagate_pin_change_locked(self, pin: int) -> None:
        st = self._pins[pin]
        old = st.last_level
        new = self._resolve_pin_locked(pin)
        if new == old:
            return
        st.last_level = new
        flag = 0
        if old == 0 and new == 1:
            flag = _IRQ_RISING
        elif old == 1 and new == 0:
            flag = _IRQ_FALLING
        handlers = list(st.irq_handlers)
        devices = list(self._devices.values())
        # Callbacks are outside lock to avoid deadlocks if user code pokes pins.
        self._lock.release()
        try:
            for irq in handlers:
                if irq.trigger & flag:
                    irq.fire(flag)
            for dev in devices:
                dev.on_pin_changed(pin, old, new)
        finally:
            self._lock.acquire()

    def _validate_pin(self, pin: int) -> None:
        if not isinstance(pin, int) or pin not in self._pins:
            raise ValueError(f"invalid GPIO pin for {self.spec.model.value}: {pin!r}")

    def i2c_scan(self) -> List[int]:
        return sorted(self._i2c_devices)

    def i2c_write(self, addr: int, data: bytes, memaddr: Optional[int] = None) -> int:
        dev = self._i2c_devices.get(addr & 0x7F)
        if dev is None:
            raise OSError(_errno.ENODEV, f"I2C address 0x{addr:02x} did not ACK")
        return dev.i2c_write(bytes(data), memaddr)

    def i2c_read(self, addr: int, nbytes: int, memaddr: Optional[int] = None) -> bytes:
        dev = self._i2c_devices.get(addr & 0x7F)
        if dev is None:
            raise OSError(_errno.ENODEV, f"I2C address 0x{addr:02x} did not ACK")
        return dev.i2c_read(nbytes, memaddr)

    def spi_transfer(self, bus_id: int, data: bytes) -> bytes:
        dev = self._spi_devices.get(bus_id)
        if dev is None:
            return bytes([0xFF] * len(data))
        return dev.spi_transfer(bytes(data))

    def uart_write(self, uart_id: int, data: bytes) -> int:
        for ep in self._uart_endpoints.get(uart_id, []):
            ep._receive_from_uart(data)
        return len(data)

    def uart_inject_rx(self, uart_id: int, data: bytes) -> None:
        q = self._uart_rx.setdefault(uart_id, queue.Queue())
        for b in data:
            q.put(b)

    def uart_any(self, uart_id: int) -> int:
        return self._uart_rx.setdefault(uart_id, queue.Queue()).qsize()

    def uart_read(self, uart_id: int, n: Optional[int] = None) -> bytes:
        q = self._uart_rx.setdefault(uart_id, queue.Queue())
        count = q.qsize() if n is None else max(0, n)
        out = bytearray()
        for _ in range(count):
            try:
                out.append(q.get_nowait())
            except queue.Empty:
                break
        return bytes(out)

    def ticks_ms(self) -> int:
        return int((_host_time.monotonic() - self._start_monotonic) * 1000) & 0x3FFFFFFF

    def ticks_us(self) -> int:
        return int((_host_time.monotonic() - self._start_monotonic) * 1_000_000) & 0x3FFFFFFF

    def ticks_cpu(self) -> int:
        return int((_host_time.monotonic() - self._start_monotonic) * self.clock_hz) & 0x3FFFFFFFFFFFFFFF


__all__ = ["PinIRQ", "SimulatedBoard"]
