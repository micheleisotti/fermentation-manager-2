"""
Controller driver interface.

Everything above this layer (acquisition, alerts, API, UI) talks to controllers only
through ControllerDriver. The simulator implements it today; the real ez2-Control
driver will implement it once we have inspected what the controller exposes.

Write operations are deliberately limited to what was agreed:
- setpoints (pH, temperature, stirrer): any time, with read-back
- pH control mode and PID parameters: only from planning, before Start
- actuators, dose counters, calibration: never (controller only)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class DriverError(RuntimeError):
    """Communication failure or unsupported operation."""


@dataclass(frozen=True)
class DriverCapabilities:
    read_values: bool = True
    read_limits: bool = False
    write_setpoints: bool = False
    write_ph_control: bool = False


@dataclass(frozen=True)
class PhControl:
    mode: Optional[str] = None            # "adaptive" | "pid"
    pid: Optional[dict] = None            # names to be confirmed on the real controller


@dataclass(frozen=True)
class ControllerSnapshot:
    ts: datetime
    ph: Optional[float] = None
    ph_setpoint: Optional[float] = None
    temperature: Optional[float] = None
    temperature_setpoint: Optional[float] = None
    stirrer: Optional[float] = None
    stirrer_setpoint: Optional[float] = None
    base_cumulative_ml: Optional[float] = None
    base_pump_pct: Optional[float] = None
    ph_low_limit: Optional[float] = None
    ph_high_limit: Optional[float] = None
    temperature_low_limit: Optional[float] = None
    temperature_high_limit: Optional[float] = None
    ph_control: PhControl = field(default_factory=PhControl)

    def setpoint(self, parameter: str) -> Optional[float]:
        return {"ph": self.ph_setpoint, "temperature": self.temperature_setpoint,
                "stirrer": self.stirrer_setpoint}[parameter]


SETPOINT_PARAMETERS = ("ph", "temperature", "stirrer")


class ControllerDriver(ABC):
    reactor_id: str
    capabilities: DriverCapabilities

    @abstractmethod
    def read(self) -> ControllerSnapshot:
        """Read current values, setpoints and (if available) limits. Raises DriverError."""

    @abstractmethod
    def write_setpoint(self, parameter: str, value: float) -> None:
        """Write one setpoint. Callers must read back to confirm. Raises DriverError."""

    @abstractmethod
    def write_ph_control(self, control: PhControl) -> None:
        """Write pH control mode and PID parameters (planning only). Raises DriverError."""
