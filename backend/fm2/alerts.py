"""
Alert engine: pure logic, no I/O, so every rule can be unit-tested with synthetic data.

One ReactorAlertState per running experiment. Feed it each poll with `observe()` (a
snapshot, or None when the controller did not answer); it returns the events to notify.

Rules (as agreed in the mockups):
- out_of_limits: immediately if outside at Start; later after `out_of_limits_after_s`
  continuously outside. One alert per excursion; re-armed after `rearm_s` back inside.
- setpoint_reached: within tolerance for `hold_s`. Once per setpoint, re-armed only when
  the setpoint changes, so dosing peaks never re-trigger it. If already within tolerance
  at Start: a single "already at setpoint" event.
- controller_silent: no successful reading for `silent_after_s`; "restored" when back.
- external_setpoint_change: setpoint changed but not by a command from this software.
- probably_finished: base rate (15-min window) below a fraction of its peak for `hold_s`.
- loop_switched_off / loop_switched_on: a control loop of a channel in use changed state without
  a command from this software (alert when it goes off, info when it comes on).
- counter_reset: the controller's base counter went back (power cycle or reset on the display);
  FM2 continues the cumulative volume (see analysis.base_dosing.stitch_counter).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .analysis.base_dosing import CounterStitcher
from .drivers.base import CHANNELS, ControllerSnapshot

LOOP_LABELS = {"ph": "pH", "temperature": "Temperature", "stirrer": "Stirrer", "do2": "dO2", "level": "Level"}

LIMITED = {"ph": ("ph", "ph_low_limit", "ph_high_limit", "pH", ""),
           "temperature": ("temperature", "temperature_low_limit", "temperature_high_limit", "T", " °C")}


@dataclass(frozen=True)
class AlertParams:
    out_of_limits_after_s: float = 180.0
    rearm_s: float = 600.0
    ph_tolerance: float = 0.05
    temp_tolerance: float = 0.5
    hold_s: float = 120.0
    silent_after_s: float = 300.0
    finished_fraction: float = 0.10
    finished_hold_s: float = 1800.0
    rate_window_s: float = 900.0


@dataclass(frozen=True)
class AlertEvent:
    kind: str
    severity: str          # "alert" | "info" | "good"
    message: str
    parameter: Optional[str] = None


def _fmt(param: str, v: float) -> str:
    return f"{v:.2f}" if param == "ph" else f"{v:.1f} °C"


@dataclass
class _LimitState:
    outside_since: Optional[datetime] = None
    inside_since: Optional[datetime] = None
    alerted: bool = False


@dataclass
class _SetpointState:
    setpoint: Optional[float] = None
    armed: bool = True
    within_since: Optional[datetime] = None


@dataclass
class ReactorAlertState:
    reactor_id: str
    params: AlertParams = field(default_factory=AlertParams)
    # channels in use: rules for a channel switched off stay silent ("controller silent" always applies)
    channels: frozenset = field(default_factory=lambda: frozenset(CHANNELS))
    pre_start: bool = False   # preparing phase: limits, setpoints and silence only (no base-dosing rules)
    _first: bool = True
    _last_ok: Optional[datetime] = None
    _silent_alerted: bool = False
    _limits: dict = field(default_factory=lambda: {"ph": _LimitState(), "temperature": _LimitState()})
    _sp: dict = field(default_factory=lambda: {"ph": _SetpointState(), "temperature": _SetpointState()})
    _observed_sp: dict = field(default_factory=dict)
    _expected_sp: dict = field(default_factory=dict)
    _cum: deque = field(default_factory=deque)
    _counter: CounterStitcher = field(default_factory=CounterStitcher)
    _peak_rate: float = 0.0
    _low_since: Optional[datetime] = None
    _finished_sent: bool = False
    _stitched: float = 0.0
    _loops: dict = field(default_factory=dict)
    _expected_loops: dict = field(default_factory=dict)
    _expected_counter_reset: int = 0     # readings left in which a drop is FM2's own reset

    @property
    def _when(self) -> str:
        return "when monitoring began" if self.pre_start else "at Start"

    # -- called by the command service before writing a setpoint ---------------------
    def expect_setpoint(self, parameter: str, value: float) -> None:
        self._expected_sp[parameter] = float(value)

    def expect_loop(self, loop: str, on: bool) -> None:
        """Called before FM2 switches a loop (and the loops it drives in cascade)."""
        self._expected_loops[loop] = bool(on)

    def expect_counter_reset(self, counter: str) -> None:
        """Called before FM2 resets a dose counter: the drop is not reported as an external reset."""
        if counter == "base":
            self._expected_counter_reset = 3

    def observe(self, snap: Optional[ControllerSnapshot], now: datetime) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        if snap is None:
            ref = self._last_ok
            if ref is not None and not self._silent_alerted and \
                    (now - ref).total_seconds() >= self.params.silent_after_s:
                self._silent_alerted = True
                mins = int((now - ref).total_seconds() // 60)
                events.append(AlertEvent("controller_silent", "alert",
                                         f"{self.reactor_id}: no reading from the controller for {mins} min"))
            if ref is None:
                self._last_ok = now  # start the silence timer from the first attempt
            return events

        if self._silent_alerted:
            events.append(AlertEvent("controller_restored", "info", f"{self.reactor_id}: controller readings restored"))
            self._silent_alerted = False
        self._last_ok = now
        first = self._first
        self._first = False

        events += self._external_changes(snap, first)
        events += self._loop_rule(snap)
        for param in ("ph", "temperature"):
            if param not in self.channels:
                continue
            events += self._limit_rule(param, snap, now, first)
            events += self._setpoint_rule(param, snap, now, first)
        if "ph" in self.channels:
            events += self._counter_rule(snap)
            events += self._finished_rule(snap, now)
        return events

    # -- rules ----------------------------------------------------------------------
    def _external_changes(self, snap: ControllerSnapshot, first: bool) -> list[AlertEvent]:
        out = []
        for param in ("ph", "temperature", "stirrer"):
            cur = snap.setpoint(param)
            if cur is None or param not in self.channels:
                continue
            prev = self._observed_sp.get(param)
            self._observed_sp[param] = cur
            if first or prev is None or abs(cur - prev) < 1e-9:
                continue
            exp = self._expected_sp.get(param)
            if exp is not None and abs(exp - cur) < 1e-6:
                del self._expected_sp[param]
                continue
            label = {"ph": "pH", "temperature": "T", "stirrer": "Stirrer"}[param]
            out.append(AlertEvent("external_setpoint_change", "alert",
                                  f"{self.reactor_id}: {label} setpoint changed on the controller ({prev:g} → {cur:g})",
                                  param))
        return out

    def _limit_rule(self, param: str, snap: ControllerSnapshot, now: datetime, first: bool) -> list[AlertEvent]:
        attr, lo_a, hi_a, label, unit = LIMITED[param]
        v, lo, hi = getattr(snap, attr), getattr(snap, lo_a), getattr(snap, hi_a)
        st: _LimitState = self._limits[param]
        if v is None or lo is None or hi is None:
            return []
        outside = v < lo or v > hi
        rng = f"{lo:g}–{hi:g}{unit}"
        if outside:
            st.inside_since = None
            if st.outside_since is None:
                st.outside_since = now
            if first and not st.alerted:
                st.alerted = True
                return [AlertEvent("out_of_limits_at_start", "alert",
                                   f"{self.reactor_id}: {label} {_fmt(param, v)} outside limits {self._when} ({rng})", param)]
            if not st.alerted and (now - st.outside_since).total_seconds() >= self.params.out_of_limits_after_s:
                st.alerted = True
                mins = int((now - st.outside_since).total_seconds() // 60)
                side = "above high" if v > hi else "below low"
                lim = hi if v > hi else lo
                return [AlertEvent("out_of_limits", "alert",
                                   f"{self.reactor_id}: {label} {_fmt(param, v)} {side} limit {lim:g}{unit} for {mins} min", param)]
        else:
            st.outside_since = None
            if st.inside_since is None:
                st.inside_since = now
            if st.alerted and (now - st.inside_since).total_seconds() >= self.params.rearm_s:
                st.alerted = False
        return []

    def _setpoint_rule(self, param: str, snap: ControllerSnapshot, now: datetime, first: bool) -> list[AlertEvent]:
        attr, _, _, label, _ = LIMITED[param]
        v, sp = getattr(snap, attr), snap.setpoint(param)
        st: _SetpointState = self._sp[param]
        if v is None or sp is None:
            return []
        if st.setpoint is None or abs(sp - st.setpoint) > 1e-9:
            st.setpoint, st.armed, st.within_since = sp, True, None
        if not st.armed:
            return []
        tol = self.params.ph_tolerance if param == "ph" else self.params.temp_tolerance
        within = abs(v - sp) <= tol
        if first and within:
            st.armed = False
            return [AlertEvent("already_at_setpoint", "good",
                               f"{self.reactor_id}: {label} already at setpoint {_fmt(param, sp)} {self._when}", param)]
        if within:
            if st.within_since is None:
                st.within_since = now
            if (now - st.within_since).total_seconds() >= self.params.hold_s:
                st.armed = False
                return [AlertEvent("setpoint_reached", "good",
                                   f"{self.reactor_id}: {label} reached setpoint {_fmt(param, sp)}", param)]
        else:
            st.within_since = None
        return []

    def _loop_rule(self, snap: ControllerSnapshot) -> list[AlertEvent]:
        out = []
        for loop, on in (snap.loops_active or {}).items():
            before = self._loops.get(loop)
            self._loops[loop] = on
            expected = self._expected_loops.get(loop)
            if expected is not None and expected == on:
                del self._expected_loops[loop]
                continue
            if before is None or before == on or loop not in self.channels:
                continue
            label = LOOP_LABELS.get(loop, loop)
            if on:
                out.append(AlertEvent("loop_switched_on", "info",
                                      f"{self.reactor_id}: {label} loop switched on at the controller", loop))
            else:
                out.append(AlertEvent("loop_switched_off", "alert",
                                      f"{self.reactor_id}: {label} loop switched OFF at the controller "
                                      f"(not by this software): its actuators no longer act", loop))
        return out

    def _counter_rule(self, snap: ControllerSnapshot) -> list[AlertEvent]:
        if self.pre_start or snap.base_cumulative_ml is None:
            return []
        before = self._counter.last
        self._stitched, reset = self._counter.update(snap.base_cumulative_ml)
        own = self._expected_counter_reset > 0
        self._expected_counter_reset = 0 if reset else max(0, self._expected_counter_reset - 1)
        if not reset or own:     # FM2's own reset is logged by the command: no second event
            return []
        return [AlertEvent("counter_reset", "info",
                           f"{self.reactor_id}: base counter reset on the controller "
                           f"({before:.2f} → {snap.base_cumulative_ml:.2f} mL); "
                           f"the cumulative volume continues from {before:.2f} mL")]

    def _finished_rule(self, snap: ControllerSnapshot, now: datetime) -> list[AlertEvent]:
        if self.pre_start or snap.base_cumulative_ml is None or self._finished_sent:
            return []
        cum = self._stitched
        self._cum.append((now, cum))
        horizon = self.params.rate_window_s
        while len(self._cum) > 2 and (now - self._cum[1][0]).total_seconds() >= horizon:
            self._cum.popleft()
        t0, c0 = self._cum[0]
        span = (now - t0).total_seconds()
        if span < horizon * 0.9:
            return []
        rate = (cum - c0) / (span / 60.0)  # mL/min
        self._peak_rate = max(self._peak_rate, rate)
        if self._peak_rate <= 0:
            return []
        if rate < self.params.finished_fraction * self._peak_rate:
            if self._low_since is None:
                self._low_since = now
            if (now - self._low_since).total_seconds() >= self.params.finished_hold_s:
                self._finished_sent = True
                return [AlertEvent("probably_finished", "info",
                                   f"{self.reactor_id}: fermentation probably finished — base rate below "
                                   f"{self.params.finished_fraction:.0%} of its peak for "
                                   f"{int(self.params.finished_hold_s // 60)} min")]
        else:
            self._low_since = None
        return []
