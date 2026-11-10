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
from ..drivers.base import PH_MODE_PARAMS, SETPOINT_PARAMETERS, ControllerDriver, DriverError, PhControl
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
    load_loop_control(driver, "ph", control, experiment_status)


def load_loop_control(driver: ControllerDriver, loop: str, control: PhControl, experiment_status: str) -> None:
    """Control type and loop parameters of pH or temperature: planning only, read back."""
    label = {"ph": "pH", "temperature": "Temperature"}.get(loop, loop)
    if experiment_status not in ("planned", "preparing"):
        raise CommandRejected(f"{label} control mode and loop parameters can only be loaded before Start")
    driver.write_loop_control(loop, control)
    after = driver.read().control(loop)
    diffs = ph_control_differences(control, after)
    if diffs:
        raise DriverError(f"{label} control read-back does not match the plan: " + "; ".join(diffs))


def ph_control_differences(plan: PhControl, ctrl: PhControl, tol: float = 1e-6) -> list[str]:
    """Human-readable differences between a planned pH control and what the controller reports."""
    out = []
    if plan.mode != ctrl.mode:
        out.append(f"mode {ctrl.mode!r} instead of {plan.mode!r}")
    got = ctrl.pid or {}
    for k, v in (plan.pid or {}).items():
        if v is None or k not in PH_MODE_PARAMS.get(plan.mode or "", ()):
            continue
        if got.get(k) is None or abs(got[k] - v) > tol:
            out.append(f"{k} {got.get(k)!r} instead of {v:g}")
    return out


LIMIT_FIELDS = {"ph": ("ph_low_limit", "ph_high_limit"),
                "temperature": ("temperature_low_limit", "temperature_high_limit")}


def write_limits(driver: ControllerDriver, parameter: str, low: Optional[float], high: Optional[float],
                 tolerance: float = 1e-6) -> list[CommandResult]:
    """Change process (alarm) limits of pH or temperature; one CommandResult per limit changed."""
    if parameter not in LIMIT_FIELDS:
        raise CommandRejected(f"Process limits can be changed for pH and temperature only, not '{parameter}'")
    if low is None and high is None:
        raise CommandRejected("Give a low and/or a high limit")
    if low is not None and high is not None and low >= high:
        raise CommandRejected(f"Low limit {low:g} must be below high limit {high:g}")
    f_lo, f_hi = LIMIT_FIELDS[parameter]
    try:
        before = driver.read()
    except DriverError as e:
        return [CommandResult(f_lo if low is not None else f_hi, None, low if low is not None else high,
                              None, "error", f"Controller not reachable: {e}")]
    wanted = [(f, v, getattr(before, f)) for f, v in ((f_lo, low), (f_hi, high)) if v is not None]
    sp = before.setpoint(parameter)
    lo_after = low if low is not None else getattr(before, f_lo)
    hi_after = high if high is not None else getattr(before, f_hi)
    warnings: tuple[str, ...] = ()
    if sp is not None and lo_after is not None and hi_after is not None and not (lo_after <= sp <= hi_after):
        warnings = (f"The current setpoint {sp:g} is outside the new limits {lo_after:g}–{hi_after:g}: "
                    "an out-of-limits alert will follow.",)
    try:
        driver.write_limits(parameter, low, high)
        after = driver.read()
    except DriverError as e:
        return [CommandResult(f, old, v, None, "error", f"Write failed: {e}", warnings) for f, v, old in wanted]
    out = []
    for f, v, old in wanted:
        rb = getattr(after, f)
        ok = rb is not None and abs(rb - v) <= tolerance
        out.append(CommandResult(f, old, v, rb, "confirmed" if ok else "mismatch",
                                 "Limit confirmed by the controller" if ok else f"Controller reports {rb!r} instead of {v:g}",
                                 warnings))
    return out
