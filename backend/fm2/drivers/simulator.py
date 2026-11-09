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
    DriverError, PhControl,
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
                ph_control=self.ph_control,
                extra={
                    "heating_pct": round(max(0.0, min(100.0, (self.sp["temperature"] - self._temp) * 40 + 25)), 1),
                    "cooling_pct": round(max(0.0, min(100.0, (self._temp - self.sp["temperature"]) * 40)), 1),
                    "stirrer_out_rpm": self.sp["stirrer"],
                    "do2": round(max(0.0, 100.0 - 25.0 * self._x + n(0, 0.5)), 1), "do2_setpoint": 30.0,
                    "do2_low_limit": 20.0, "do2_high_limit": 120.0, "n2_pct": 0.0, "air_pct": 0.0,
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
