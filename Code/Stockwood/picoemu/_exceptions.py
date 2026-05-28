"""Exception classes for picoemu."""


class PicoSimError(Exception):
    """Base simulation exception."""


class ImportBlockedError(ImportError):
    """Raised when a target script imports a module outside the simulated set."""


class PinContentionError(PicoSimError):
    """Raised when multiple outputs drive a pin to conflicting levels."""


class SimulationStopped(BaseException):
    """Internal exception used to unwind target scripts when stop() is requested."""


__all__ = [
    "PicoSimError",
    "ImportBlockedError",
    "PinContentionError",
    "SimulationStopped",
]
