from .base import ControllerDriver, ControllerSnapshot, DriverCapabilities, DriverError, PhControl
from .simulator import SimulatedController


def make_driver(kind: str, reactor_id: str, address: str = "") -> ControllerDriver:
    if kind == "simulator":
        return SimulatedController(reactor_id)
    if kind == "ez2control":
        from .ez2control import Ez2ControlDriver
        return Ez2ControlDriver(reactor_id, address)
    raise ValueError(f"Unknown driver: {kind}")


__all__ = [
    "ControllerDriver", "ControllerSnapshot", "DriverCapabilities", "DriverError", "PhControl",
    "SimulatedController", "make_driver",
]
