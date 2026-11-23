"""
Controller driver interface.

Everything above this layer (acquisition, alerts, API, UI) talks to controllers only
through ControllerDriver. The simulator implements it today; the real ez2-Control
driver will implement it once we have inspected what the controller exposes.

Write operations are deliberately limited to what was agreed:
- setpoints (pH, temperature, stirrer): any time, with read-back
- process (alarm) limits of pH and temperature: any time, with read-back (decision 9 Nov 2026)
- pH and temperature control mode and loop parameters: only from planning, before Start
- loop on/off (start/stop of a control loop, decision 10 Nov 2026): only loops allowed in
  Settings, with confirmation, read-back and an event
- actuators, actuator-to-loop assignments, dose counters, calibration, interlock limits:
  never (controller only); assignments are read (read_loop_config) and shown in planning
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
    write_limits: bool = False


@dataclass(frozen=True)
class PhControl:
    mode: Optional[str] = None            # "adaptive" | "pid"
    # pH loop parameters: P, I, D, deadband, cycle_time [s], settling_time [s] (ez2-Control names)
    pid: Optional[dict] = None
    # Whether the controller uses a dead band on this loop (1.29.<sensor>). The ez2-Control only shows
    # and applies "Dead Band" when it is enabled (on R1/R2: pH yes, temperature no). None = unknown.
    deadband_enabled: Optional[bool] = None


# Dose counters FM2 may reset (decision 13 Nov 2026), with the snapshot value they reset:
# "base_cumulative_ml" is a ControllerSnapshot field, the others are extra fields.
DOSE_COUNTERS = {"base": "base_cumulative_ml", "acid": "acid_cumulative_ml", "antifoam": "antifoam_cumulative_ml"}
DOSE_COUNTER_LABELS = {"base": "Base (Alkali)", "acid": "Addition 2 (acid)", "antifoam": "Antifoam"}


def counter_value(snap: "ControllerSnapshot", counter: str) -> Optional[float]:
    field_ = DOSE_COUNTERS[counter]
    return snap.base_cumulative_ml if field_ == "base_cumulative_ml" else (snap.extra or {}).get(field_)


LoopControl = PhControl   # same structure for the temperature loop

# Loops whose control type and loop parameters a profile can load (decision 10 Nov 2026: the
# "safe" loops pH and temperature).
CONTROL_LOOPS = ("ph", "temperature")

# Parameters a profile loads for each control mode (pH and temperature alike). In Adaptive, P/I/D are the starting
# values the controller then adapts (decision 9 Nov 2026: load them in Adaptive too).
PH_MODE_PARAMS = {"pid": ("P", "I", "D", "deadband", "cycle_time"),
                  "adaptive": ("P", "I", "D", "deadband", "cycle_time", "settling_time")}


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
    temperature_control: PhControl = field(default_factory=PhControl)
    # loop on/off as reported by the controller: {"ph": True, ...} (loops it could read)
    loops_active: dict = field(default_factory=dict)
    # Optional channels (see CHANNELS): dO2 and actuator outputs, keyed by EXTRA_FIELDS
    extra: dict = field(default_factory=dict)

    def control(self, loop: str) -> PhControl:
        return {"ph": self.ph_control, "temperature": self.temperature_control}[loop]

    def setpoint(self, parameter: str) -> Optional[float]:
        return {"ph": self.ph_setpoint, "temperature": self.temperature_setpoint,
                "stirrer": self.stirrer_setpoint}[parameter]


SETPOINT_PARAMETERS = ("ph", "temperature", "stirrer")

# Channels an experiment can use (chosen at planning). A channel switched off is still recorded,
# but it is not shown and raises no alerts.
CHANNELS = ("temperature", "ph", "stirrer", "do2", "level", "redox")
DEFAULT_CHANNELS = ("temperature", "ph", "stirrer")
# Extra values carried in ControllerSnapshot.extra, with the channel they belong to.
EXTRA_FIELDS = {
    "heating_pct": "temperature", "cooling_pct": "temperature",
    "stirrer_out_rpm": "stirrer",
    "do2": "do2", "do2_setpoint": "do2", "do2_low_limit": "do2", "do2_high_limit": "do2",
    "n2_pct": "do2", "air_pct": "do2",
    # pH-high (acid) pump = Addition 2 when assigned to the pH loop
    "acid_cumulative_ml": "ph", "acid_pump_pct": "ph",
    "level_contact": "level", "antifoam_cumulative_ml": "level", "antifoam_pct": "level",
    "redox_mv": "redox",
    # loop on/off at each reading (1/0), from snapshot.loops_active
    "loop_ph": "ph", "loop_temperature": "temperature", "loop_stirrer": "stirrer", "loop_do2": "do2", "loop_level": "level",
}


# --- loop configuration (read from the controller, shown in planning) ---------------------

@dataclass(frozen=True)
class LoopActuator:
    id: int                   # actuator index, or sensor index for kind == "sensor" (cascade)
    name: str
    side: int                 # +n: n-th actuator acting below the setpoint, -n: above
    kind: str = "actuator"    # "actuator" | "sensor" (another loop used as actuator, cascade)
    pump: bool = False        # dosing pump (volume in mL once calibrated)
    calibrated: Optional[bool] = None
    dose_monitoring: Optional[bool] = None
    unit: str = ""


@dataclass(frozen=True)
class LoopInfo:
    key: str                  # "ph", "temperature", "stirrer", "do2", "level", "redox"
    sensor: int
    name: str
    available: int            # 0 absent, 1 not installed, 3 installed, 4 selected as control loop
    active: Optional[bool] = None
    control_type: Optional[str] = None
    value: Optional[str] = None
    setpoint: Optional[float] = None
    has_actuator: Optional[bool] = None
    actuators: tuple = ()     # LoopActuator
    drives: Optional[str] = None   # key of the loop this sensor drives in cascade
    probe: Optional[str] = None    # level probe: "Foam"/"Level" and sensitivity


@dataclass(frozen=True)
class LoopConfig:
    loops: tuple              # LoopInfo
    unassigned_pumps: tuple = ()   # LoopActuator (side 0) installed but in no loop
    free_outputs: tuple = ()       # every installed output in no loop (pumps, gas valves, ...) — 2.3
    not_installed: tuple = ()      # (kind "input"|"output", id, name): known to the controller, not installed


@dataclass(frozen=True)
class ManualState:
    """What a manual (prime) command on one pump depends on, read just before writing it."""
    pump_id: int
    name: str
    assigned_loop_on: bool
    controlled: bool
    interlocked: bool
    manual_on: bool
    prime_on: bool
    counter_ml: Optional[float]



class ControllerDriver(ABC):
    reactor_id: str
    capabilities: DriverCapabilities

    @abstractmethod
    def read(self) -> ControllerSnapshot:
        """Read current values, setpoints and (if available) limits. Raises DriverError."""

    def close(self) -> None:
        """Release the connection (driver replaced or bioreactor disabled in Settings)."""

    @abstractmethod
    def write_setpoint(self, parameter: str, value: float) -> None:
        """Write one setpoint. Callers must read back to confirm. Raises DriverError."""

    @abstractmethod
    def write_ph_control(self, control: PhControl) -> None:
        """Write pH control mode and PID parameters (planning only). Raises DriverError."""

    def write_loop_control(self, loop: str, control: PhControl) -> None:
        """Control type and loop parameters of 'ph' or 'temperature' (planning only)."""
        if loop == "ph":
            return self.write_ph_control(control)
        raise DriverError(f"this controller driver cannot load the {loop} loop parameters")

    def write_loop_active(self, loop: str, on: bool) -> None:
        """Switch one control loop on or off. Callers read back (snapshot.loops_active)."""
        raise DriverError("this controller driver cannot switch loops")

    def read_loop_config(self) -> LoopConfig:
        """Loops, their actuators (and cascades) and calibration state. Raises DriverError."""
        raise DriverError("this controller driver cannot read the loop configuration")

    def manual_state(self, pump_id: int) -> "ManualState":
        raise DriverError("this controller driver has no manual pump commands")

    def write_prime(self, pump_id: int, on: bool) -> None:
        """Prime ("Click & Hold To Prime", 2.46) of one pump on/off. The controller does not stop it by itself."""
        raise DriverError("this controller driver has no manual pump commands")

    def write_loop_assignment(self, loop: str, actuator_id: int, side: int, cascade: bool = False) -> None:
        """Add an actuator to a loop (side +1 below / -1 above the setpoint) or remove it (side 0)."""
        raise DriverError("this controller driver cannot change the loop configuration")

    def write_channel_values(self, channel: str, setpoint: Optional[float], low: Optional[float], high: Optional[float]) -> dict:
        """Setpoint and process limits of dO2 / stirrer limits (2.3). Returns the read-back values."""
        raise DriverError(f"this controller driver cannot write {channel} values")

    def reset_dose_counter(self, counter: str) -> None:
        """Reset one dose counter (DOSE_COUNTERS) to 0, like the controller's own "Reset Dose Monitor".
        Callers read back (snapshot fields in DOSE_COUNTERS)."""
        raise DriverError("this controller driver cannot reset dose counters")

    def write_limits(self, parameter: str, low: Optional[float], high: Optional[float]) -> None:
        """
        Write the process (alarm) limits of 'ph' or 'temperature'; None leaves a limit as it is.
        Callers must read back. Raises DriverError. Interlock limits are never written.
        """
        raise DriverError("this controller driver cannot write process limits")
