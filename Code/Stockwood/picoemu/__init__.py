"""Raspberry Pi Pico/Pico W/Pico 2 simulation harness for CPython.

This package preserves the original single-module API while exposing a package
layout that is easier to navigate from editor tools and imports.
"""

from .board import SimulatedBoard
from .constants import (
    BOARD_SPECS,
    DEFAULT_SYS_CLOCK_HZ,
    FLOATING,
    LOGIC_HIGH,
    LOGIC_LOW,
    BoardModel,
    BoardSpec,
    PinLevel,
)
from .devices import (
    ButtonDevice,
    I2CDevice,
    I2CMemoryDevice,
    LedDevice,
    LoopbackSPIDevice,
    PinDevice,
    PinState,
    SPIDevice,
    UARTEndpoint,
    WireDevice,
)
from .errors import ImportBlockedError, PicoSimError, PinContentionError
from .simulator import PicoSimulator, RunResult, ScriptRun

__all__ = [
    "BoardModel",
    "BoardSpec",
    "BOARD_SPECS",
    "DEFAULT_SYS_CLOCK_HZ",
    "FLOATING",
    "PinLevel",
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
    "SimulatedBoard",
    "PicoSimulator",
    "RunResult",
    "ScriptRun",
    "PicoSimError",
    "ImportBlockedError",
    "PinContentionError",
    "LOGIC_LOW",
    "LOGIC_HIGH",
]
