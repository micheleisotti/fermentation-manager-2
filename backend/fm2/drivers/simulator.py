"""
Simulated ez2-Control, for development without bioreactors.

The model is deliberately simple but reproduces what the UI and the alert rules need:
- logistic growth after a lag; acid production proportional to growth
- pH falls until it crosses the setpoint, then the controller doses base in discrete
  shots: each dose makes pH jump up a little (the small peaks seen on the real trace)
  and adds a fixed volume to the cumulative counter (a staircase, not a smooth curve)
- temperature approaches its setpoint with first-order dynamics (cooling slower)
- stirrer follows its setpoint

`speed` accelerates simulated time (e.g. 60 = one simulated minute per real second).
`clock` is injectable so tests can drive time deterministically.
"""

from __future__ import annotations

import math
import random
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .base import (
    SETPOINT_PARAMETERS, ControllerDriver, ControllerSnapshot, DriverCapabilities,
    DriverError, LoopActuator, LoopConfig, LoopInfo, PhControl,
)

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SimulatedController(ControllerDriver):
    capabilities = DriverCapabilities(read_values=True, read_limits=True,
                                      write_setpoints=True, write_ph_control=True, write_limits=True)

    def __init__(self, reactor_id: str, *, clock: Clock = _utcnow, speed: float = 1.0,
                 seed: Optional[int] = None, lag_h: float = 4.0, mu_max: float = 0.9,
                 dose_ml: float = 0.4, initial_ph: float = 6.55):
        self.reactor_id = reactor_id
        self._clock = clock
        self._speed = speed
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self.lag_h, self.mu_max, self.dose_ml, self.initial_ph = lag_h, mu_max, dose_ml, initial_ph
        self.fail_reads = False  # tests: simulate a silent controller

        self.sp = {"ph": 6.0, "temperature": 30.0, "stirrer": 200.0}
        self.limits = {"ph": (5.8, 6.3), "temperature": (28.0, 32.0)}
        self.ph_control = PhControl(mode="pid", pid={"P": 1.0, "I": 60.0, "D": 0.0})
        self.temperature_control = PhControl(mode="adaptive", pid={"P": 50.0, "I": 6000.0, "D": 20.0,
                                                                   "settling_time": 1500.0})
        self.loops_active = {"ph": False, "temperature": False, "stirrer": False, "do2": False, "level": False}
        # loop configuration as found on R2-dx (10 Nov 2026); tests may replace it
        self.loop_config = LoopConfig(loops=(
            LoopInfo("ph", 0, "pH", 4, False, "pid", "6.87", 6.0, True, (
                LoopActuator(29, "Alkali", 1, pump=True, calibrated=True, dose_monitoring=True, unit="ml/min"),
                LoopActuator(28, "Addition 2", -1, pump=True, calibrated=False, dose_monitoring=False, unit="ml/min"))),
            LoopInfo("temperature", 2, "Temperature", 4, False, "adaptive", "22.6", 30.0, True, (
                LoopActuator(19, "Heating", 1), LoopActuator(46, "Cooling", -1))),
            LoopInfo("stirrer", 13, "Stirrer", 4, False, "external", "0", 200.0, True,
                     (LoopActuator(18, "Stirrer", 1),), drives="do2"),
            LoopInfo("do2", 1, "dO2", 4, False, "pid", "0.0", 200.0, True,
                     (LoopActuator(13, "Stirrer", 1, kind="sensor"),)),
            LoopInfo("level", 3, "Foam", 4, False, "level", "NO CONT", 0.0, False, (),
                     probe="Foam probe, sensitivity High"),
            LoopInfo("redox", 32, "Redox", 1, False, "pid", "0.000", 0.0, False, ()),
        ), unassigned_pumps=(LoopActuator(30, "Antifoam", 0, pump=True, calibrated=False, dose_monitoring=True, unit="%"),),
            free_outputs=(LoopActuator(0, "N2 Valve", 0, unit="%"), LoopActuator(1, "Air Valve", 0, unit="%"),
                          LoopActuator(30, "Antifoam", 0, pump=True, calibrated=False, dose_monitoring=True, unit="%")),
            not_installed=(("output", 2, "O2 Valve"), ("output", 3, "CO2 Valve"), ("output", 5, "AnalogOut 1"),
                           ("output", 38, "N2 MFC"), ("input", 5, "Analog In 1"), ("input", 23, "DigitalIn 1")))
        self.prime_on: dict[int, bool] = {}
        self.reset()

    # -- simulation control -------------------------------------------------------
    def reset(self) -> None:
        """New culture: called when an experiment starts in simulation mode."""
        with self._lock:
            self._last_real = self._clock()
            self._t_h = 0.0
            self._x = 0.08
            self._ph = self.initial_ph
            self._temp = self.sp["temperature"] - 1.4
            self._base_ml = 0.0
            self._acid_ml = 0.0
            self._antifoam_ml = 0.0
            self._pump_pct = 0.0

    on_experiment_start = reset

    def _advance(self) -> None:
        now = self._clock()
        dt_h = max(0.0, (now - self._last_real).total_seconds()) * self._speed / 3600.0
        self._last_real = now
        self._pump_pct = 0.0
        step = 10.0 / 3600.0  # 10 simulated seconds
        while dt_h > 1e-12:
            h = min(step, dt_h)
            self._step(h)
            dt_h -= h

    def _step(self, h: float) -> None:
        self._t_h += h
        x_max = 4.4
        growing = self._t_h > self.lag_h
        dx = self.mu_max * self._x * (1 - self._x / x_max) * h if growing else 0.0
        self._x += dx
        # acid production lowers pH (growth-associated + small maintenance term)
        self._ph -= (0.9 * dx + 0.002 * self._x * h) * 0.6
        if self.prime_on.get(29):          # manual prime of the alkali pump: 4.8 mL/min
            self._base_ml += 4.8 * 60 * h
        ph_sp = self.sp["ph"]
        if self._ph < ph_sp - 0.01:
            self._base_ml += self.dose_ml
            self._ph += 0.045
            self._pump_pct = max(self._pump_pct, 35 + 25 * self._rng.random())
        t_sp = self.sp["temperature"]
        tau_h = 0.25 if t_sp >= self._temp else 0.6
        self._temp += (t_sp - self._temp) * (1 - math.exp(-h / tau_h))

    # -- driver interface ------------------------------------------------------
    def read(self) -> ControllerSnapshot:
        if self.fail_reads:
            raise DriverError(f"{self.reactor_id}: no response (simulated)")
        with self._lock:
            self._advance()
            n = self._rng.gauss
            return ControllerSnapshot(
                ts=self._clock(),
                ph=round(self._ph + n(0, 0.003), 3),
                ph_setpoint=self.sp["ph"],
                temperature=round(self._temp + n(0, 0.03), 2),
                temperature_setpoint=self.sp["temperature"],
                stirrer=round(self.sp["stirrer"] + n(0, 1.0), 0),
                stirrer_setpoint=self.sp["stirrer"],
                base_cumulative_ml=round(self._base_ml, 2),
                base_pump_pct=round(self._pump_pct, 1),
                ph_low_limit=self.limits["ph"][0],
                ph_high_limit=self.limits["ph"][1],
                temperature_low_limit=self.limits["temperature"][0],
                temperature_high_limit=self.limits["temperature"][1],
                ph_control=self.ph_control, temperature_control=self.temperature_control,
                loops_active=dict(self.loops_active),
                extra={
                    "heating_pct": round(max(0.0, min(100.0, (self.sp["temperature"] - self._temp) * 40 + 25)), 1),
                    "cooling_pct": round(max(0.0, min(100.0, (self._temp - self.sp["temperature"]) * 40)), 1),
                    "stirrer_out_rpm": self.sp["stirrer"],
                    "do2": round(max(0.0, 100.0 - 25.0 * self._x + n(0, 0.5)), 1), "do2_setpoint": self._cv("do2", "setpoint", 30.0),
                    "do2_low_limit": self._cv("do2", "low", 20.0), "do2_high_limit": self._cv("do2", "high", 120.0),
                    "stirrer_low_limit": self._cv("stirrer", "low", 20.0), "stirrer_high_limit": self._cv("stirrer", "high", 500.0), "n2_pct": 0.0, "air_pct": 0.0,
                    "acid_cumulative_ml": round(self._acid_ml, 2), "antifoam_cumulative_ml": round(self._antifoam_ml, 2),
                },
            )

    def write_setpoint(self, parameter: str, value: float) -> None:
        if parameter not in SETPOINT_PARAMETERS:
            raise DriverError(f"Unknown setpoint '{parameter}'")
        with self._lock:
            self._advance()
            self.sp[parameter] = float(value)

    def write_limits(self, parameter: str, low, high) -> None:
        if parameter not in self.limits:
            raise DriverError(f"no process limits for {parameter!r}")
        lo, hi = self.limits[parameter]
        lo, hi = (lo if low is None else low), (hi if high is None else high)
        if lo >= hi:
            raise DriverError("low limit must be below the high limit")
        with self._lock:
            self.limits[parameter] = (lo, hi)

    def write_ph_control(self, control: PhControl) -> None:
        with self._lock:
            self.ph_control = control

    def write_loop_control(self, loop: str, control: PhControl) -> None:
        if loop not in ("ph", "temperature"):
            raise DriverError(f"no loop parameters for {loop!r}")
        with self._lock:
            setattr(self, f"{loop}_control", control)

    def write_loop_active(self, loop: str, on: bool) -> None:
        if loop not in self.loops_active:
            raise DriverError(f"cannot switch the {loop!r} loop")
        with self._lock:
            self.loops_active[loop] = bool(on)
            # like the controller: a loop drives the loops it uses in cascade (dO2 -> stirrer)
            for lp in self.loop_config.loops:
                if lp.drives == loop and lp.key in self.loops_active:
                    self.loops_active[lp.key] = bool(on)

    def reset_dose_counter(self, counter: str) -> None:
        attr = {"base": "_base_ml", "acid": "_acid_ml", "antifoam": "_antifoam_ml"}.get(counter)
        if attr is None:
            raise DriverError(f"cannot reset the {counter!r} counter")
        with self._lock:
            self._advance()
            setattr(self, attr, 0.0)

    def manual_state(self, pump_id: int):
        from .base import ManualState
        names = {29: "Alkali", 28: "Addition 2", 30: "Antifoam"}
        if pump_id not in names:
            raise DriverError(f"actuator {pump_id} is not a pump")
        loop_on = pump_id in (28, 29) and self.loops_active.get("ph", False)
        with self._lock:
            self._advance()
            count = {29: self._base_ml, 28: self._acid_ml, 30: self._antifoam_ml}[pump_id]
        return ManualState(pump_id, names[pump_id], loop_on, loop_on, False, False, self.prime_on.get(pump_id, False), count)

    def write_prime(self, pump_id: int, on: bool) -> None:
        with self._lock:
            self._advance()
            self.prime_on[pump_id] = bool(on)

    def write_loop_assignment(self, loop: str, actuator_id: int, side: int, cascade: bool = False) -> None:
        from dataclasses import replace
        if self.loops_active.get(loop):
            raise DriverError(f"the {loop} loop is on: stop it before changing its actuators")
        with self._lock:
            names = {a.id: a for lp in self.loop_config.loops for a in lp.actuators}
            names.update({a.id: a for a in self.loop_config.free_outputs})
            act = names.get(actuator_id) or LoopActuator(actuator_id, f"Actuator {actuator_id}", 0)
            loops = []
            for lp in self.loop_config.loops:
                acts = tuple(a for a in lp.actuators if a.id != actuator_id)
                if lp.key == loop and side != 0:
                    acts = acts + (replace(act, side=side),)
                loops.append(replace(lp, actuators=acts))
            used = {a.id for lp in loops for a in lp.actuators}
            free = tuple(replace(a, side=0) for a in names.values() if a.id not in used and a.kind == "actuator"
                         and a.id not in (18, 19, 46, 13))
            self.loop_config = replace(self.loop_config, loops=tuple(loops), free_outputs=free,
                                       unassigned_pumps=tuple(a for a in free if a.pump))

    def _cv(self, ch: str, key: str, default: float) -> float:
        v = getattr(self, "channel_values", {}).get(ch, {}).get(key)
        return default if v is None else v

    def write_channel_values(self, channel: str, setpoint, low, high) -> dict:
        if channel not in ("do2", "stirrer"):
            raise DriverError(f"no channel values for {channel!r}")
        self.channel_values = getattr(self, "channel_values", {})
        cur = self.channel_values.setdefault(channel, {"setpoint": None, "low": None, "high": None})
        for k, v in (("setpoint", setpoint), ("low", low), ("high", high)):
            if v is not None:
                cur[k] = v
        return dict(cur)

    def read_loop_config(self) -> LoopConfig:
        if self.fail_reads:
            raise DriverError(f"{self.reactor_id}: no response (simulated)")
        from dataclasses import replace
        return replace(self.loop_config, loops=tuple(
            replace(lp, active=self.loops_active.get(lp.key, lp.active)) for lp in self.loop_config.loops))

    # -- helpers for tests -------------------------------------------------------
    @property
    def simulated_hours(self) -> float:
        return self._t_h


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, start: Optional[datetime] = None):
        self.now = start or datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)
