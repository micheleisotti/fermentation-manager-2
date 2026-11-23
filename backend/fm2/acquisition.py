"""
Acquisition: polls every controller at its own interval (Settings > Bioreactors & probes), stores
readings for the experiment currently fermenting on that reactor, runs the alert engine and stores
events. The last 3 hours of live values are also kept in memory (never in the database) for the
Trends of a bioreactor without a running experiment.

It runs in the same service process as the API (a background thread), and that process
is started at Windows boot, so recording never depends on a browser being open.
Controller readings keep flowing over the local Ethernet even if the Wi-Fi drops.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .alerts import AlertParams, ReactorAlertState
from .config import IDLE_HISTORY_S
from .db import session_scope
from .drivers.base import DEFAULT_CHANNELS
from .drivers.base import ControllerDriver, ControllerSnapshot, DriverError
from .models import ControllerReading, Event, Experiment, ExperimentStatus

log = logging.getLogger("fm2.acquisition")


@dataclass
class LiveState:
    snapshot: Optional[ControllerSnapshot] = None
    last_ok: Optional[datetime] = None
    last_error: Optional[str] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


HISTORY_FIELDS = ("ph", "ph_setpoint", "temperature", "temperature_setpoint", "stirrer", "stirrer_setpoint",
                  "base_cumulative_ml", "base_pump_pct", "ph_low_limit", "ph_high_limit",
                  "temperature_low_limit", "temperature_high_limit")


class Acquisition:
    def __init__(self, drivers: dict[str, ControllerDriver], session_factory: sessionmaker[Session],
                 interval_s: float = 5.0, alert_params: AlertParams = AlertParams(),
                 clock: Callable[[], datetime] = _utcnow,
                 notifier: Optional[Callable[[Event], None]] = None,
                 intervals: Optional[dict[str, float]] = None):
        self.drivers = drivers
        self.sessions = session_factory
        self.interval_s = interval_s                      # default for reactors without their own interval
        self.intervals: dict[str, float] = dict(intervals or {})
        self.history: dict[str, deque] = {r: deque() for r in drivers}
        self._next_due: dict[str, float] = {}
        self.alert_params = alert_params
        self.clock = clock
        self.notifier = notifier
        self.live: dict[str, LiveState] = {r: LiveState() for r in drivers}
        self.alert_states: dict[int, ReactorAlertState] = {}  # by experiment id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def alert_state_for(self, experiment_id: int, reactor_id: str, pre_start: bool = False) -> ReactorAlertState:
        """One state per experiment and phase: Start begins with a fresh state ("at Start" checks again)."""
        key = (experiment_id, "pre") if pre_start else experiment_id
        if key not in self.alert_states:
            self.alert_states[key] = ReactorAlertState(reactor_id, self.alert_params, pre_start=pre_start)
        return self.alert_states[key]

    # -- configuration (Settings > Bioreactors & probes) ---------------------------------
    def interval_of(self, reactor_id: str) -> float:
        return self.intervals.get(reactor_id, self.interval_s)

    def set_reactor(self, reactor_id: str, driver: ControllerDriver, interval_s: float) -> None:
        """Add or replace a reactor's driver (new address, driver or interval) without stopping the others."""
        with self._lock:
            old = self.drivers.get(reactor_id)
            self.drivers[reactor_id] = driver
            self.intervals[reactor_id] = interval_s
            self.live.setdefault(reactor_id, LiveState())
            self.history.setdefault(reactor_id, deque())
            if old is not driver:
                self.live[reactor_id] = LiveState()
                self.history[reactor_id] = deque()
            self._next_due[reactor_id] = 0.0
        if old is not None and old is not driver:
            try:
                old.close()
            except Exception:  # closing an old connection must never stop the new one
                log.exception("closing old driver failed")

    def set_interval(self, reactor_id: str, interval_s: float) -> None:
        with self._lock:
            self.intervals[reactor_id] = interval_s
            self._next_due[reactor_id] = 0.0

    def remove_reactor(self, reactor_id: str) -> None:
        with self._lock:
            old = self.drivers.pop(reactor_id, None)
            self.live.pop(reactor_id, None)
            self.history.pop(reactor_id, None)
            self._next_due.pop(reactor_id, None)
        if old is not None:
            try:
                old.close()
            except Exception:
                log.exception("closing driver failed")

    def history_of(self, reactor_id: str) -> list[dict]:
        with self._lock:
            return list(self.history.get(reactor_id, ()))

    # -- polling ----------------------------------------------------------------------
    def poll_once(self) -> None:
        """Read every reactor now (tests and manual refresh)."""
        for reactor_id in list(self.drivers):
            self.poll_reactor(reactor_id)

    def poll_reactor(self, reactor_id: str) -> None:
        with self._lock:
            driver = self.drivers.get(reactor_id)
            if driver is None:
                return
            now = self.clock()
            snap: Optional[ControllerSnapshot] = None
            live = self.live[reactor_id]
            try:
                snap = driver.read()
                live.snapshot, live.last_ok, live.last_error = snap, now, None
                self._remember(reactor_id, snap, now)
            except DriverError as e:
                live.last_error = str(e)
                log.warning("%s read failed: %s", reactor_id, e)
            self._record(reactor_id, snap, now)

    @staticmethod
    def _dosed_so_far(s: Session, experiment_id: int) -> float:
        """Base dosed since Start from the stored readings (counter resets stitched), e.g. after a restart."""
        from .analysis.base_dosing import stitch_counter
        vals = [v for v in s.scalars(select(ControllerReading.base_cumulative_ml)
                                     .where(ControllerReading.experiment_id == experiment_id)
                                     .order_by(ControllerReading.t_h)) if v is not None]
        if len(vals) < 2:
            return 0.0
        st = stitch_counter(vals)
        return float(st[-1] - st[0])

    def _remember(self, reactor_id: str, snap: ControllerSnapshot, now: datetime) -> None:
        h = self.history.setdefault(reactor_id, deque())
        row = {"ts": now.isoformat(), **{k: getattr(snap, k, None) for k in HISTORY_FIELDS}}
        if snap.extra:
            row.update({k: v for k, v in snap.extra.items() if isinstance(v, (int, float))})
        h.append(row)
        cutoff = now.timestamp() - IDLE_HISTORY_S
        while h and datetime.fromisoformat(h[0]["ts"]).timestamp() < cutoff:
            h.popleft()

    def _record(self, reactor_id: str, snap: Optional[ControllerSnapshot], now: datetime) -> None:
        new_events: list[Event] = []
        with session_scope(self.sessions) as s:
            exp = s.scalars(select(Experiment).where(
                Experiment.reactor_id == reactor_id,
                Experiment.status.in_((ExperimentStatus.fermenting, ExperimentStatus.preparing)))).first()
            if exp is None:
                return
            pre = exp.status == ExperimentStatus.preparing
            t0 = exp.prepared_at if pre else exp.started_at
            if t0 is None:
                return
            # preparing: hours since "Go to fermentation" (events only; shifted to negative times at Start)
            t_h = (now - _as_utc(t0)).total_seconds() / 3600.0
            if snap is not None and not pre:   # nothing is recorded before Start: live values and alerts only
                s.add(ControllerReading(
                    experiment_id=exp.id, ts=now, t_h=t_h,
                    ph=snap.ph, ph_setpoint=snap.ph_setpoint,
                    temperature=snap.temperature, temperature_setpoint=snap.temperature_setpoint,
                    stirrer=snap.stirrer, stirrer_setpoint=snap.stirrer_setpoint,
                    base_cumulative_ml=snap.base_cumulative_ml, base_pump_pct=snap.base_pump_pct,
                    ph_low_limit=snap.ph_low_limit, ph_high_limit=snap.ph_high_limit,
                    temperature_low_limit=snap.temperature_low_limit,
                    temperature_high_limit=snap.temperature_high_limit,
                    # loop on/off recorded too (1/0), for the analysis of the regulation (2.1.3)
                    extra=(dict(snap.extra) | {f"loop_{k}": 1.0 if on else 0.0
                                               for k, on in (snap.loops_active or {}).items()}) or None,
                ))
            state = self.alert_state_for(exp.id, reactor_id, pre_start=pre)
            state.channels = frozenset(exp.channels or DEFAULT_CHANNELS)   # may change during the run
            state.buffer_volume_ml = exp.buffer_volume_ml
            if not pre and not state.buffer_ready:
                state.buffer_dosed_before_ml = self._dosed_so_far(s, exp.id)
                state.buffer_ready = True
            for ev in state.observe(snap, now):
                row = Event(experiment_id=exp.id, reactor_id=reactor_id, kind=ev.kind,
                            severity=ev.severity, message=ev.message, t_h=t_h, at=now)
                s.add(row)
                new_events.append(row)
        if self.notifier:
            for ev in new_events:
                try:
                    self.notifier(ev)
                except Exception:  # a failing notification channel must never stop recording
                    log.exception("notification failed")

    # -- background thread ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fm2-acquisition", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        """Each reactor is read when its own interval has elapsed (2-300 s), in one thread."""
        while not self._stop.is_set():
            t = time.monotonic()
            for rid in list(self.drivers):
                if self._next_due.get(rid, 0.0) <= t:
                    self._next_due[rid] = t + self.interval_of(rid)
                    try:
                        self.poll_reactor(rid)
                    except Exception:
                        log.exception("poll of %s failed", rid)
            due = [d for d in self._next_due.values()] or [t + 1.0]
            self._stop.wait(max(0.2, min(min(due) - time.monotonic(), 1.0)))
