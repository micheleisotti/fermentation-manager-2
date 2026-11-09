"""
Safe setpoint writes.

Every command: validate (known parameter, allowed range, max step) → tell the alert
engine to expect the change (so it is not flagged as "changed on the controller") →
write → read back → log the outcome. pH control mode / PID can only be written while
the experiment is still planned (from the planning screen), never during fermentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..config import SetpointRange
from ..drivers.base import SETPOINT_PARAMETERS, ControllerDriver, DriverError, PhControl
from ..alerts import ReactorAlertState


class CommandRejected(ValueError):
    pass


@dataclass(frozen=True)
class CommandResult:
    parameter: str
    old_value: Optional[float]
    requested_value: float
    readback_value: Optional[float]
    status: str          # "confirmed" | "mismatch" | "error"
    message: str
    warnings: tuple[str, ...] = ()


def validate_setpoint(parameter: str, value: float, current: Optional[float],
                      ranges: dict[str, SetpointRange]) -> None:
    if parameter not in SETPOINT_PARAMETERS:
        raise CommandRejected(f"Unknown setpoint '{parameter}'")
    r = ranges[parameter]
    if not (r.minimum <= value <= r.maximum):
        raise CommandRejected(f"{parameter} {value:g} outside allowed range {r.minimum:g}–{r.maximum:g}")
    if current is not None and abs(value - current) > r.max_step:
        raise CommandRejected(f"{parameter}: change of {abs(value - current):g} exceeds the maximum step {r.max_step:g}")


def limit_warnings(parameter: str, value: float, snap) -> tuple[str, ...]:
    """Warn (not block) when the new setpoint sits outside the controller's process limits."""
    if parameter == "ph":
        lo, hi, unit = snap.ph_low_limit, snap.ph_high_limit, ""
    elif parameter == "temperature":
        lo, hi, unit = snap.temperature_low_limit, snap.temperature_high_limit, " °C"
    else:
        return ()
    if lo is not None and value < lo:
        return (f"{value:g}{unit} is below the low process limit ({lo:g}{unit}) set on the controller: "
                "an out-of-limits alert will follow unless the limit is changed.",)
    if hi is not None and value > hi:
        return (f"{value:g}{unit} is above the high process limit ({hi:g}{unit}) set on the controller: "
                "an out-of-limits alert will follow unless the limit is changed.",)
    return ()


def write_setpoint(driver: ControllerDriver, parameter: str, value: float,
                   ranges: dict[str, SetpointRange], alert_state: Optional[ReactorAlertState] = None,
                   tolerance: float = 1e-6) -> CommandResult:
    try:
        before = driver.read()
    except DriverError as e:
        return CommandResult(parameter, None, value, None, "error", f"Controller not reachable: {e}")
    current = before.setpoint(parameter)
    validate_setpoint(parameter, value, current, ranges)
    warnings = limit_warnings(parameter, value, before)
    if alert_state is not None:
        alert_state.expect_setpoint(parameter, value)
    try:
        driver.write_setpoint(parameter, value)
        after = driver.read()
    except DriverError as e:
        return CommandResult(parameter, current, value, None, "error", f"Write failed: {e}", warnings)
    rb = after.setpoint(parameter)
    if rb is not None and abs(rb - value) <= tolerance:
        return CommandResult(parameter, current, value, rb, "confirmed", "Setpoint confirmed by the controller", warnings)
    return CommandResult(parameter, current, value, rb, "mismatch",
                         f"Controller reports {rb!r} instead of {value:g}", warnings)


def load_ph_control(driver: ControllerDriver, control: PhControl, experiment_status: str) -> None:
    if experiment_status != "planned":
        raise CommandRejected("pH control mode and PID can only be loaded from planning, before Start")
    driver.write_ph_control(control)
    after = driver.read().ph_control
    if after.mode != control.mode or (control.pid is not None and after.pid != control.pid):
        raise DriverError("pH control read-back does not match the profile")
