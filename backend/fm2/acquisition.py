"""
Acquisition: polls every controller at a fixed interval, stores readings for the
experiment currently fermenting on that reactor, runs the alert engine and stores events.

It runs in the same service process as the API (a background thread), and that process
is started at Windows boot, so recording never depends on a browser being open.
Controller readings keep flowing over the local Ethernet even if the Wi-Fi drops.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .alerts import AlertParams, ReactorAlertState
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


class Acquisition:
    def __init__(self, drivers: dict[str, ControllerDriver], session_factory: sessionmaker[Session],
                 interval_s: float = 30.0, alert_params: AlertParams = AlertParams(),
                 clock: Callable[[], datetime] = _utcnow,
                 notifier: Optional[Callable[[Event], None]] = None):
        self.drivers = drivers
        self.sessions = session_factory
        self.interval_s = interval_s
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

    def poll_once(self) -> None:
        with self._lock:
            now = self.clock()
            for reactor_id, driver in self.drivers.items():
                snap: Optional[ControllerSnapshot] = None
                live = self.live[reactor_id]
                try:
                    snap = driver.read()
                    live.snapshot, live.last_ok, live.last_error = snap, now, None
                except DriverError as e:
                    live.last_error = str(e)
                    log.warning("%s read failed: %s", reactor_id, e)
                self._record(reactor_id, snap, now)

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
            # preparing: hours since "Go to fermentation"; shifted to negative times at Start
            t_h = (now - _as_utc(t0)).total_seconds() / 3600.0
            if snap is not None:
                s.add(ControllerReading(
                    experiment_id=exp.id, ts=now, t_h=t_h,
                    ph=snap.ph, ph_setpoint=snap.ph_setpoint,
                    temperature=snap.temperature, temperature_setpoint=snap.temperature_setpoint,
                    stirrer=snap.stirrer, stirrer_setpoint=snap.stirrer_setpoint,
                    base_cumulative_ml=snap.base_cumulative_ml, base_pump_pct=snap.base_pump_pct,
                    ph_low_limit=snap.ph_low_limit, ph_high_limit=snap.ph_high_limit,
                    temperature_low_limit=snap.temperature_low_limit,
                    temperature_high_limit=snap.temperature_high_limit,
                    extra=dict(snap.extra) or None,
                ))
            state = self.alert_state_for(exp.id, reactor_id, pre_start=pre)
            state.channels = frozenset(exp.channels or DEFAULT_CHANNELS)   # may change during the run
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
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("poll failed")
            self._stop.wait(self.interval_s)
