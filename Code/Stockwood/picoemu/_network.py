"""network module stub for Pico W / Pico 2 W behavior."""

from __future__ import annotations

import errno as _errno
import types
from typing import Any, List, Optional, Tuple

from ._board import SimulatedBoard


class _WLAN:
    STAT_IDLE = 0
    STAT_CONNECTING = 1
    STAT_WRONG_PASSWORD = -3
    STAT_NO_AP_FOUND = -2
    STAT_CONNECT_FAIL = -1
    STAT_GOT_IP = 3

    board: SimulatedBoard

    def __init__(self, interface: int):
        self.interface = interface
        self._active = False
        self._ssid: Optional[str] = None

    def active(self, value: Optional[bool] = None) -> bool:
        if value is not None:
            self._active = bool(value)
        return self._active

    def connect(self, ssid: str, key: Optional[str] = None, *args: Any, **kwargs: Any) -> None:
        if not self.board.spec.wireless:
            raise OSError(_errno.ENODEV, "wireless hardware is not present on this board model")
        self._ssid = ssid
        self._active = True
        self.board.network_connected = True
        self.board.network_config = ("192.168.0.123", "255.255.255.0", "192.168.0.1", "192.168.0.1")

    def disconnect(self) -> None:
        self.board.network_connected = False

    def isconnected(self) -> bool:
        return self.board.network_connected

    def status(self, param: Any = None) -> int:
        if not self._active:
            return self.STAT_IDLE
        return self.STAT_GOT_IP if self.board.network_connected else self.STAT_CONNECTING

    def ifconfig(self, config: Optional[Tuple[str, str, str, str]] = None):
        if config is not None:
            self.board.network_config = config
        return self.board.network_config

    def config(self, *args: Any, **kwargs: Any) -> Any:
        if args and args[0] == "mac":
            return b"\x02PI\x00\x00\x01"
        if "essid" in kwargs:
            self._ssid = kwargs["essid"]
        return None

    def scan(self) -> List[Tuple[bytes, bytes, int, int, int, bool]]:
        return []


def _make_network_module(board: SimulatedBoard) -> types.ModuleType:
    mod = types.ModuleType("network")
    _WLAN.board = board
    mod.WLAN = _WLAN
    mod.STA_IF = 0
    mod.AP_IF = 1
    mod.STAT_IDLE = _WLAN.STAT_IDLE
    mod.STAT_CONNECTING = _WLAN.STAT_CONNECTING
    mod.STAT_GOT_IP = _WLAN.STAT_GOT_IP
    return mod


__all__ = ["_WLAN", "_make_network_module"]
