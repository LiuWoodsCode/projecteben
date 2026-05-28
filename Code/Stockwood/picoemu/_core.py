"""Compatibility facade for legacy imports.

Historically picoemu lived in a single module. The implementation now lives in
smaller internal modules, but this file keeps import paths stable for any caller
that still imports symbols from picoemu._core.
"""

from ._board import PinIRQ, SimulatedBoard
from ._constants import (
    BOARD_SPECS,
    DEFAULT_SYS_CLOCK_HZ,
    FLOATING,
    LOGIC_HIGH,
    LOGIC_LOW,
    PinLevel,
    BoardModel,
    BoardSpec,
)
from ._devices import (
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
from ._exceptions import ImportBlockedError, PicoSimError, PinContentionError, SimulationStopped
from ._simulator_impl import PicoSimulator, RunResult, ScriptRun

__all__ = [
    "BoardModel",
    "BoardSpec",
    "BOARD_SPECS",
    "DEFAULT_SYS_CLOCK_HZ",
    "FLOATING",
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
]
