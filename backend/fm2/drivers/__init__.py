from .base import ControllerDriver, ControllerSnapshot, DriverCapabilities, DriverError, PhControl
from .simulator import SimulatedController


def make_driver(kind: str, reactor_id: str, address: str = "", clock=None) -> ControllerDriver:
    if kind == "simulator":
        params = {"R2": {"lag_h": 5.5, "mu_max": 0.7}}.get(reactor_id, {})
        return SimulatedController(reactor_id, **({"clock": clock} if clock else {}), **params)
    if kind == "ez2control":
        from .ez2control import Ez2ControlDriver
        return Ez2ControlDriver(reactor_id, address)
    raise ValueError(f"Unknown driver: {kind}")


__all__ = [
    "ControllerDriver", "ControllerSnapshot", "DriverCapabilities", "DriverError", "PhControl",
    "SimulatedController", "make_driver",
]
