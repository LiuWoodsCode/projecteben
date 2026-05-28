"""Runtime helpers shared across simulated modules."""

from __future__ import annotations

import time as _host_time
from typing import Optional

from ._board import SimulatedBoard


def _sim_sleep(board: SimulatedBoard, amount: Optional[float], scale: float) -> None:
    if amount is None:
        amount = 0
    deadline = _host_time.monotonic() + float(amount) / scale
    while _host_time.monotonic() < deadline:
        board.check_stopped()
        _host_time.sleep(min(0.01, max(0, deadline - _host_time.monotonic())))


__all__ = ["_sim_sleep"]
