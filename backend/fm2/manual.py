"""
Manual pumps, "hold to run" (2.3). Verified on the ez2-Control (23 Nov 2026): the prime command 2.46 runs
the pump until it is written back to 0 — the controller never stops it by itself, not even when FM2
disconnects, and it does not block it while the pump's loop is on. So FM2 does all of it:

- before switching on: refuses if the pump's loop is on, the pump is under loop control, interlocked,
  or already in manual on the controller;
- while on: the app sends "still held" about every 0.5 s; without it for HOLD_TIMEOUT_S the pump is
  switched off; never longer than MAX_RUN_S per press;
- every pump FM2 switches on is written to a small file; at start-up FM2 switches those off again;
- each run is an event with duration and mL (from the pump's dose counter).

Remaining risk (stated in the UI): if the lab PC or FM2 stops while a pump is on, the pump keeps running
until it is stopped on the controller's display.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .drivers.base import ControllerDriver, DriverError

log = logging.getLogger(__name__)

HOLD_TIMEOUT_S = 1.5
MAX_RUN_S = 60.0


class ManualRefused(Exception):
    pass


@dataclass
class Run:
    reactor_id: str
    pump_id: int
    name: str
    started: float
    last_seen: float
    start_ml: Optional[float]
    author: str
    ml_now: Optional[float] = None


@dataclass
class ManualPumps:
    drivers: dict
    state_file: Path
    on_stop: Callable[[Run, str, Optional[float]], None] = lambda run, why, ml: None
    clock: Callable[[], float] = time.monotonic
    runs: dict = field(default_factory=dict)          # (reactor, pump) -> Run
    last_stop: dict = field(default_factory=dict)     # (reactor, pump) -> why it stopped last
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    # -- persistence: what FM2 switched on, to switch it off after a restart --------------------
    def _save(self) -> None:
        try:
            self.state_file.write_text(json.dumps([[r, p] for r, p in self.runs]))
        except OSError as ex:
            log.warning("manual pumps: cannot write %s: %s", self.state_file, ex)

    def recover(self) -> list[tuple[str, int]]:
        """At start-up: switch off every pump left on by a previous run of FM2."""
        try:
            left = [tuple(x) for x in json.loads(self.state_file.read_text())]
        except (OSError, ValueError):
            left = []
        done = []
        for reactor, pump in left:
            drv = self.drivers.get(reactor)
            if drv is None:
                continue
            try:
                drv.write_prime(int(pump), False)
                done.append((reactor, int(pump)))
                log.warning("manual pumps: %s pump %s switched off at start-up (left on by a previous run)", reactor, pump)
            except DriverError as ex:
                log.error("manual pumps: cannot switch off %s pump %s at start-up: %s", reactor, pump, ex)
        try:
            self.state_file.write_text("[]")
        except OSError:
            pass
        return done

    # -- commands -----------------------------------------------------------------------------
    def hold(self, reactor_id: str, pump_id: int, author: str, start: bool = True) -> Run:
        """start=True on press; start=False for the keep-alive while held: it never restarts a stopped pump."""
        drv: ControllerDriver = self.drivers[reactor_id]
        key = (reactor_id, pump_id)
        with self._lock:
            run = self.runs.get(key)
            if run is not None:
                run.last_seen = self.clock()
                return run
            if not start:
                raise ManualRefused(f"stopped ({self.last_stop.get(key, 'released')}): release the button and press again")
        st = drv.manual_state(pump_id)
        why = ("its loop is on" if st.assigned_loop_on else "it is under loop control" if st.controlled
               else "it is interlocked" if st.interlocked else "it is already in manual on the controller" if st.manual_on else None)
        if why:
            raise ManualRefused(f"{st.name}: not started, {why}.")
        drv.write_prime(pump_id, True)
        now = self.clock()
        run = Run(reactor_id, pump_id, st.name, now, now, st.counter_ml, author)
        with self._lock:
            self.runs[key] = run
            self._save()
        return run

    def release(self, reactor_id: str, pump_id: int, why: str = "released") -> Optional[Run]:
        with self._lock:
            run = self.runs.pop((reactor_id, pump_id), None)
            self.last_stop[(reactor_id, pump_id)] = why
            self._save()
        drv = self.drivers.get(reactor_id)
        ml = None
        try:
            if drv is not None:
                drv.write_prime(pump_id, False)
        except DriverError as ex:
            log.error("manual pumps: cannot switch off %s pump %s: %s", reactor_id, pump_id, ex)
            if run is not None:                      # keep trying from the watchdog
                with self._lock:
                    run.last_seen = -1e9
                    self.runs[(reactor_id, pump_id)] = run
                    self._save()
            raise
        if run is not None:
            try:
                after = drv.manual_state(pump_id).counter_ml if drv is not None else None
                ml = None if after is None or run.start_ml is None else max(0.0, after - run.start_ml)
            except DriverError:
                pass
            self.on_stop(run, why, ml)
        return run

    def stop_all(self, why: str = "FM2 stopping") -> None:
        for key in list(self.runs):
            try:
                self.release(*key, why=why)
            except DriverError:
                pass

    # -- watchdog -----------------------------------------------------------------------------
    def check(self) -> None:
        now = self.clock()
        with self._lock:
            due = [(k, "signal lost" if now - r.last_seen > HOLD_TIMEOUT_S else "60 s limit")
                   for k, r in self.runs.items() if now - r.last_seen > HOLD_TIMEOUT_S or now - r.started > MAX_RUN_S]
        for (reactor, pump), why in due:
            try:
                self.release(reactor, pump, why=why)
            except DriverError:
                pass

    def start(self) -> None:
        def loop():
            while not self._stop.wait(0.25):
                try:
                    self.check()
                except Exception:          # never let the watchdog die
                    log.exception("manual pumps watchdog")
        self._thread = threading.Thread(target=loop, name="manual-pumps", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.stop_all()
