"""Shared constants and board model metadata for picoemu."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

DEFAULT_SYS_CLOCK_HZ = 125_000_000
LOGIC_LOW = 0
LOGIC_HIGH = 1
FLOATING = None
PinLevel = Optional[int]


class BoardModel(str, enum.Enum):
    PICO = "pico"
    PICO_W = "pico_w"
    PICO_2 = "pico_2"
    PICO_2_W = "pico_2_w"


@dataclass(frozen=True)
class BoardSpec:
    model: BoardModel
    chip: str
    wireless: bool
    gpio_count: int = 30
    default_sys_clock_hz: int = DEFAULT_SYS_CLOCK_HZ
    flash_filesystem_kb: int = 1600
    onboard_led_pin: Union[int, str] = 25
    adc_gpio: Tuple[int, ...] = (26, 27, 28, 29)
    adc_channels: int = 5


BOARD_SPECS: Dict[BoardModel, BoardSpec] = {
    BoardModel.PICO: BoardSpec(BoardModel.PICO, "RP2040", False, onboard_led_pin=25),
    BoardModel.PICO_W: BoardSpec(BoardModel.PICO_W, "RP2040", True, onboard_led_pin="CYW43_LED"),
    BoardModel.PICO_2: BoardSpec(BoardModel.PICO_2, "RP2350", False, onboard_led_pin=25),
    BoardModel.PICO_2_W: BoardSpec(BoardModel.PICO_2_W, "RP2350", True, onboard_led_pin="CYW43_LED"),
}


# MicroPython-ish names for stdlib modules. The values are CPython module names.
_ALLOWED_STDLIB_MODULES = {
    "array": "array",
    "asyncio": "asyncio",
    "binascii": "binascii",
    "builtins": "builtins",
    "cmath": "cmath",
    "collections": "collections",
    "errno": "errno",
    "gc": "gc",
    "gzip": "gzip",
    "hashlib": "hashlib",
    "heapq": "heapq",
    "io": "io",
    "json": "json",
    "marshal": "marshal",
    "math": "math",
    "os": "os",
    "platform": "platform",
    "random": "random",
    "re": "re",
    "select": "select",
    "socket": "socket",
    "ssl": "ssl",
    "struct": "struct",
    "sys": "sys",
    "time": "time",
    "weakref": "weakref",
    "zlib": "zlib",
    "_thread": "_thread",
    # Python 3.14+; this will fail naturally on older interpreters if imported.
    "string.templatelib": "string.templatelib",
}

# MicroPython compatibility aliases and Pico-specific modules.
_ALLOWED_SIM_MODULES = {
    "machine",
    "micropython",
    "rp2",
    "network",
    "utime",
    "uasyncio",
    "ubinascii",
    "ujson",
    "uos",
    "ustruct",
}


__all__ = [
    "BoardModel",
    "BoardSpec",
    "BOARD_SPECS",
    "DEFAULT_SYS_CLOCK_HZ",
    "LOGIC_LOW",
    "LOGIC_HIGH",
    "FLOATING",
    "PinLevel",
    "_ALLOWED_STDLIB_MODULES",
    "_ALLOWED_SIM_MODULES",
]
