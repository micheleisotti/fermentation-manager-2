"""
Applikon ez2-Control driver — READ-ONLY for now.

How the controller talks (found by inspecting the web UI, firmware mE.2.9.2.4,
see docs/controller-inspection.md):

- WebSocket at ws://<controller-ip>/ , binary frames carrying ASCII text.
- Every datum has a hierarchical address, e.g. 1.15.0 = sensor 0 (pH) process value.
- Read request:   "J<address>S\\r"        e.g. b"J1.15.0S\\r"
  Answer:         "J<address>A<value>\\r" e.g. b"J1.15.0A6.90\\r"
  Several requests may share one frame; answers may arrive split over several frames.
- Write request:  "J<address>s<value>\\r" (lowercase s), value as plain text, exactly as the
  web UI's setpoint dialog sends it (rightpanelsensors.js -> controlData.setpoint()).
  This driver writes ONLY the three setpoint addresses in SETPOINT_ADDRESSES; every write
  goes through _write_checked(), which validates against the controller's own range and
  decimals and waits for the controller to report the new value.
- No authentication is needed for reading. Writing needs a login (levels: 0 View,
  1 Operator, 2 Engineer, 3 Service):  "J0.11s<level>|<user>|<base64(password)>\\r",
  confirmed by reading 0.11 ("<level>|<user>|..."); logout is "J0.11s0|0|\\r".
  The controller allows one login at a time and offers to "claim" it from another user:
  this driver NEVER claims. It logs in only around a single setpoint write and logs out
  immediately; if the login is refused (wrong password, or someone logged in on the
  display/web UI), nothing is written.

Addresses used here are identical on R1-sx (192.0.2.42) and R2-dx (192.0.2.41).
The base pump is actuator 29 ("Alkali"), assigned to the pH loop on the "below setpoint"
side. Its dose-monitoring counter 2.13.29 is the cumulative volume in mL (verified:
20 s at 4.80 mL/min -> 1.61 mL).
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

from .base import (PH_MODE_PARAMS, ControllerDriver, ControllerSnapshot, DriverCapabilities, DriverError,
                   PhControl)

log = logging.getLogger(__name__)

# Sensor indices (second level of 1.x.<sensor>)
PH, TEMPERATURE, STIRRER = 0, 2, 13
BASE_ACTUATOR = 29

# What read() asks for: key -> address
READ_MAP: dict[str, str] = {
    "ph":                       f"1.15.{PH}",
    "ph_setpoint":              f"1.13.{PH}",
    "ph_high_limit":            f"1.4.{PH}",
    "ph_low_limit":             f"1.5.{PH}",
    "ph_control_type":          f"1.19.{PH}",
    "ph_pid_p":                 f"3.0.{PH}",
    "ph_pid_i":                 f"3.3.{PH}",
    "ph_pid_d":                 f"3.6.{PH}",
    "ph_deadband":              f"3.9.{PH}",
    "ph_cycle_time":            f"3.12.{PH}",
    "ph_settling_time":         f"3.71.{PH}",
    "temperature":              f"1.15.{TEMPERATURE}",
    "temperature_setpoint":     f"1.13.{TEMPERATURE}",
    "temperature_high_limit":   f"1.4.{TEMPERATURE}",
    "temperature_low_limit":    f"1.5.{TEMPERATURE}",
    "stirrer":                  f"1.15.{STIRRER}",
    "stirrer_setpoint":         f"1.13.{STIRRER}",
    "base_name":                f"2.1.{BASE_ACTUATOR}",
    "base_cumulative_ml":       f"2.13.{BASE_ACTUATOR}",
    "base_flow":                f"2.12.{BASE_ACTUATOR}",
    "base_factor":              f"2.4.{BASE_ACTUATOR}",
}

# The only addresses this driver may ever write.
SETPOINT_SENSORS = {"ph": PH, "temperature": TEMPERATURE, "stirrer": STIRRER}
SETPOINT_ADDRESSES = frozenset(f"1.13.{i}" for i in SETPOINT_SENSORS.values())

# pH loop: control type and PID terms (written only from planning, before Start)
PH_TYPE_ADDRESS = f"1.19.{PH}"
# Loop parameters as named in the controller's "Controller setup" window
# (controllersetupstandard.js): P, I, D, Dead Band, Cycle Time [s], Settling Time [s, Adaptive].
PH_PID_ADDRESSES = {"P": f"3.0.{PH}", "I": f"3.3.{PH}", "D": f"3.6.{PH}", "deadband": f"3.9.{PH}",
                    "cycle_time": f"3.12.{PH}", "settling_time": f"3.71.{PH}"}
PH_PID_LIMITS = {"P": (f"3.2.{PH}", f"3.1.{PH}"), "I": (f"3.5.{PH}", f"3.4.{PH}"),   # (min, max) addresses
                 "D": (f"3.8.{PH}", f"3.7.{PH}"), "deadband": (f"3.11.{PH}", f"3.10.{PH}"),
                 "cycle_time": (f"3.14.{PH}", f"3.13.{PH}")}
SETTLING_TIME_RANGE = (1.0, 86400.0)   # the controller exposes no min/max for it
# Only PID (0) and Adaptive (1). External (2) would re-purpose the pumps: never written.
WRITABLE_PH_MODES = {"pid": "0", "adaptive": "1"}

WRITABLE_ADDRESSES = SETPOINT_ADDRESSES | {PH_TYPE_ADDRESS} | frozenset(PH_PID_ADDRESSES.values())


def build_write_request(address: str, text: str) -> bytes:
    if address not in WRITABLE_ADDRESSES:
        raise DriverError(f"refusing to write {address}: not an address FM2 may write")
    if not text or any(c in text for c in "\rJSs|"):
        raise DriverError(f"refusing to write malformed value {text!r}")
    if address == PH_TYPE_ADDRESS and text not in WRITABLE_PH_MODES.values():
        raise DriverError(f"refusing to set pH control type {text!r}: only PID or Adaptive")
    return f"J{address}s{text}\r".encode("ascii")


LOGIN_ADDRESS = "0.11"
DEFAULT_PASSWORD_FILE = r"C:\ProgramData\FermentationManager\ez2-password.txt"


def build_login_request(level: int, user: int, password: str) -> bytes:
    if level not in (1, 2):
        raise DriverError("login level must be 1 (Operator) or 2 (Engineer)")
    token = base64.b64encode(password.encode("utf-8")).decode("ascii")
    return f"J{LOGIN_ADDRESS}s{level}|{int(user)}|{token}\r".encode("ascii")


LOGOUT_REQUEST = f"J{LOGIN_ADDRESS}s0|0|\r".encode("ascii")


def load_password(path: str | None = None) -> str | None:
    """Controller password from a file outside the repository (first line), or None."""
    p = Path(path or os.environ.get("FM2_EZ2_PASSWORD_FILE", DEFAULT_PASSWORD_FILE))
    try:
        return p.read_text(encoding="utf-8").splitlines()[0].strip() or None
    except (OSError, IndexError):
        return None


# sensorData.type() for a loop, as listed in the web UI (controllersetupstandard.js)
CONTROL_TYPES = {0: "pid", 1: "adaptive", 2: "external", 3: "level", 4: "flow", 5: "p-adaptive", 6: "tcusp"}


def build_read_request(addresses) -> bytes:
    """One frame with a read request per address. Only uppercase 'S' (read) is ever produced."""
    return "".join(f"J{a}S\r" for a in addresses).encode("ascii")


def parse_frame(payload: bytes | str) -> dict[str, str]:
    """
    Parse one frame into {address: raw value}, mirroring the web UI's webSocketMessage():
    a line is data if it has no 'E', or if its 'A' comes before the first 'E'
    (values such as 1.0E+05 contain an E). Other lines are status/errors and are ignored.
    """
    text = payload.decode("latin-1") if isinstance(payload, (bytes, bytearray)) else payload
    out: dict[str, str] = {}
    for line in text.split("\r"):
        line = line.strip("\n")
        if not line:
            continue
        a, e = line.find("A"), line.find("E")
        if e < 0 or (a > -1 and a < e):
            if a < 1:
                continue
            out[line[1:a]] = line[a + 1:]
    return out


def to_float(raw: Optional[str]) -> Optional[float]:
    """Numeric value, or None for missing / non-numeric readings such as 'NO CONT'."""
    if raw is None:
        return None
    try:
        return float(raw.strip())
    except ValueError:
        return None


def snapshot_from_values(values: dict[str, str], ts: datetime) -> ControllerSnapshot:
    """Build a ControllerSnapshot from {READ_MAP key: raw value}."""
    f = lambda k: to_float(values.get(k))  # noqa: E731

    flow, factor = f("base_flow"), f("base_factor")
    pump_pct = (flow / factor * 100.0) if (flow is not None and factor) else None

    ctype = f("ph_control_type")
    mode = CONTROL_TYPES.get(int(ctype), f"type-{int(ctype)}") if ctype is not None else None
    pid = {"P": f("ph_pid_p"), "I": f("ph_pid_i"), "D": f("ph_pid_d"), "deadband": f("ph_deadband"),
           "cycle_time": f("ph_cycle_time"), "settling_time": f("ph_settling_time")}
    pid = {k: v for k, v in pid.items() if v is not None} or None

    return ControllerSnapshot(
        ts=ts,
        ph=f("ph"), ph_setpoint=f("ph_setpoint"),
        temperature=f("temperature"), temperature_setpoint=f("temperature_setpoint"),
        stirrer=f("stirrer"), stirrer_setpoint=f("stirrer_setpoint"),
        base_cumulative_ml=f("base_cumulative_ml"), base_pump_pct=pump_pct,
        ph_low_limit=f("ph_low_limit"), ph_high_limit=f("ph_high_limit"),
        temperature_low_limit=f("temperature_low_limit"),
        temperature_high_limit=f("temperature_high_limit"),
        ph_control=PhControl(mode=mode, pid=pid),
    )


class Ez2ControlDriver(ControllerDriver):
    """Reads one ez2-Control over its WebSocket. Keeps one connection open, reconnects on failure."""

    capabilities = DriverCapabilities(read_values=True, read_limits=True,
                                      write_setpoints=True, write_ph_control=True)

    def __init__(self, reactor_id: str, address: str, timeout_s: float = 5.0,
                 connect: Optional[Callable] = None, clock: Optional[Callable[[], datetime]] = None,
                 login_level: Optional[int] = None, login_user: Optional[int] = None,
                 password_file: Optional[str] = None):
        if not address:
            raise ValueError(f"{reactor_id}: ez2control driver needs an address (FM2_{reactor_id}_ADDRESS)")
        self.reactor_id = reactor_id
        self.address = address
        self.url = address if address.startswith("ws://") else f"ws://{address}/"
        self.timeout_s = timeout_s
        self._connect = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ws = None
        self._lock = threading.Lock()
        self._checked_base_name = False
        self._connected_once = False
        self.login_level = int(login_level if login_level is not None else os.environ.get("FM2_EZ2_LEVEL", "2"))
        self.login_user = int(login_user if login_user is not None else os.environ.get("FM2_EZ2_USER", "0"))
        self.password_file = password_file

    # -- connection ------------------------------------------------------------------
    def _open(self):
        if self._ws is None:
            connect = self._connect
            if connect is None:
                from websockets.sync.client import connect
            try:
                # __enter__() gives a connection object on both old (<17) and new websockets
                # releases; we keep it open across reads and close it ourselves in _drop().
                # ping_interval=None: the controller's WebSocket server is minimal; we do not
                # rely on protocol pings to keep the link alive (see _query for reconnects).
                self._ws = connect(self.url, open_timeout=self.timeout_s, close_timeout=1,
                                   ping_interval=None, max_size=2 ** 22,
                                   compression=None).__enter__()
            except Exception as e:  # network errors come in many types
                raise DriverError(f"cannot connect to {self.url}: {e}") from e
            # The controller drops idle links, so we reconnect on almost every poll:
            # log the first connection only, the rest at debug level.
            log.log(logging.DEBUG if self._connected_once else logging.INFO,
                    "%s connected to %s", self.reactor_id, self.url)
            self._connected_once = True
        return self._ws

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _query(self, keys: dict[str, str]) -> dict[str, str]:
        """
        Send read requests and collect answers. The controller silently drops connections
        that stay idle for a few seconds (observed on both units: every read after a 5 s
        pause failed with "no close frame received or sent"), so a failure on a reused
        connection is retried once on a fresh one.
        """
        reused = self._ws is not None
        try:
            return self._query_once(keys)
        except DriverError:
            if not reused:
                raise
            log.debug("%s: stale connection, reconnecting", self.reactor_id)
            return self._query_once(keys)

    def _query_once(self, keys: dict[str, str]) -> dict[str, str]:
        import time
        ws = self._open()
        wanted = set(keys.values())
        got: dict[str, str] = {}
        try:
            ws.send(build_read_request(keys.values()))
            deadline = time.monotonic() + self.timeout_s
            while wanted - got.keys():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    frame = ws.recv(timeout=remaining)
                except TimeoutError:
                    break
                got.update({a: v for a, v in parse_frame(frame).items() if a in wanted})
        except Exception as e:
            self._drop()
            if not got:
                raise DriverError(f"{self.url}: {e}") from e
            # link dropped after some answers arrived: keep them, the rest stays empty
            log.warning("%s: connection closed during a read (%s); %d/%d values received",
                        self.reactor_id, e, len(got), len(wanted))
        if not got:
            self._drop()
            raise DriverError(f"{self.url}: no answer within {self.timeout_s:.0f} s")
        missing = wanted - got.keys()
        if missing:
            log.warning("%s: no answer for %s", self.reactor_id, sorted(missing))
        return {k: got[a] for k, a in keys.items() if a in got}

    # -- ControllerDriver -------------------------------------------------------------
    def read(self) -> ControllerSnapshot:
        with self._lock:
            values = self._query(READ_MAP)
        name = values.get("base_name", "")
        if not self._checked_base_name and name:
            self._checked_base_name = True
            if "alkali" not in name.lower():
                log.warning("%s: actuator %d is named %r, expected 'Alkali' — base volume may be wrong",
                            self.reactor_id, BASE_ACTUATOR, name)
        return snapshot_from_values(values, self._clock())

    # -- writes ----------------------------------------------------------------------
    def _logged_in_writes(self, writes: list[tuple[str, str, int]]) -> None:
        """
        Log in, send each (address, text, decimals) write and wait for its read-back, log out.
        Caller holds self._lock and has validated the values. Never claims another login.
        """
        import time
        frames = [(a, t, d, build_write_request(a, t)) for a, t, d in writes]   # allow-list first
        password = load_password(self.password_file)
        if password is None:
            raise DriverError("no controller password configured (ez2-password.txt): not writing")
        self._drop()                       # fresh connection: login state is per connection
        ws = self._open()
        logged_in = False
        try:
            try:
                ws.send(build_login_request(self.login_level, self.login_user, password))
            except Exception as e:
                raise DriverError(f"{self.url}: login failed: {e}") from e
            level, until = None, time.monotonic() + 2.0
            while time.monotonic() < until:
                state = self._query_once({"login": LOGIN_ADDRESS}).get("login", "")
                level = to_float(state.split("|")[0]) if state else None
                if level is not None and level >= self.login_level:
                    break
                time.sleep(0.2)
            if level is None or level < self.login_level:
                raise DriverError("login refused by the controller (wrong password, or another user is "
                                  "logged in on the display/web UI). Nothing was written.")
            logged_in = True
            for address, text, dec, frame in frames:
                try:
                    self._ws.send(frame)
                except Exception as e:
                    raise DriverError(f"{self.url}: write of {address} failed: {e}") from e
                log.info("%s: %s -> %s sent", self.reactor_id, address, text)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    got = to_float(self._query_once({"v": address}).get("v"))
                    if got is not None and abs(got - float(text)) < 10 ** (-dec) / 2:
                        break
                    time.sleep(0.25)
                # callers read back and report a mismatch if the value did not change
        finally:
            if logged_in and self._ws is not None:
                try:
                    self._ws.send(LOGOUT_REQUEST)
                    self._query_once({"login": LOGIN_ADDRESS})
                except Exception:
                    log.warning("%s: logout after write could not be confirmed", self.reactor_id)
            self._drop()

    def write_setpoint(self, parameter: str, value: float) -> None:
        """
        Write one setpoint (pH, temperature, stirrer). Validates against the controller's own
        sensor range and display decimals, then waits until the controller reports the new
        value. services/commands.py adds the agreed ranges/max step and the read-back record.
        """
        import math
        if parameter not in SETPOINT_SENSORS:
            raise DriverError(f"unknown setpoint {parameter!r}")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DriverError(f"invalid value {value!r}")
        sensor = SETPOINT_SENSORS[parameter]
        with self._lock:
            info = self._query({"max": f"1.6.{sensor}", "min": f"1.7.{sensor}", "decimals": f"1.58.{sensor}"})
            lo, hi, dec = to_float(info.get("min")), to_float(info.get("max")), to_float(info.get("decimals"))
            if lo is None or hi is None or dec is None:
                raise DriverError(f"{parameter}: cannot read the controller's range/decimals, not writing")
            dec = int(dec)
            if not (lo <= value <= hi):
                raise DriverError(f"{parameter} {value:g} outside the controller's range {lo:g}–{hi:g}")
            text = _format(value, dec, parameter)
            self._logged_in_writes([(f"1.13.{sensor}", text, dec)])

    def write_ph_control(self, control: PhControl) -> None:
        """
        Load the pH control type (PID or Adaptive) and its loop parameters — from planning
        only (services/commands.load_ph_control enforces 'planned'). PID: P, I, D, dead band,
        cycle time. Adaptive: the same with P/I/D as starting values, plus settling time. Checked against
        the controller's own min/max; only values that differ are written, in one login session.
        """
        import math
        if control is None or control.mode not in WRITABLE_PH_MODES:
            raise DriverError(f"pH control mode must be one of {sorted(WRITABLE_PH_MODES)}, got "
                              f"{getattr(control, 'mode', None)!r}")
        params = {k: v for k, v in (control.pid or {}).items() if v is not None}
        unknown = set(params) - set(PH_PID_ADDRESSES)
        if unknown:
            raise DriverError(f"unknown pH loop parameters {sorted(unknown)}: expected {sorted(PH_PID_ADDRESSES)}")
        params = {k: v for k, v in params.items() if k in PH_MODE_PARAMS[control.mode]}
        with self._lock:
            keys = {"type": PH_TYPE_ADDRESS}
            for term in params:
                keys[term] = PH_PID_ADDRESSES[term]
                if term in PH_PID_LIMITS:
                    keys[term + "_min"], keys[term + "_max"] = PH_PID_LIMITS[term]
            cur = self._query(keys)
            writes: list[tuple[str, str, int]] = []
            want_type = WRITABLE_PH_MODES[control.mode]
            if to_float(cur.get("type")) != float(want_type):
                writes.append((PH_TYPE_ADDRESS, want_type, 0))
            for term, value in params.items():
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise DriverError(f"pH {term}: invalid value {value!r}")
                if term in PH_PID_LIMITS:
                    lo, hi = to_float(cur.get(term + "_min")), to_float(cur.get(term + "_max"))
                else:
                    lo, hi = SETTLING_TIME_RANGE
                if lo is None or hi is None:
                    raise DriverError(f"pH {term}: cannot read the controller's range, not writing")
                if not (lo <= value <= hi):
                    raise DriverError(f"pH {term} {value:g} outside the controller's range {lo:g}–{hi:g}")
                raw = cur.get(term, "")
                if to_float(raw) is None:
                    raise DriverError(f"pH {term}: cannot read the current value, not writing")
                dec = len(raw.split(".")[1]) if "." in raw else 0   # controller's display decimals
                text = _format(value, dec, f"pH {term}")
                if abs(to_float(raw) - float(text)) >= 10 ** (-dec) / 2:
                    writes.append((PH_PID_ADDRESSES[term], text, dec))
            if writes:
                self._logged_in_writes(writes)


def _format(value: float, dec: int, what: str) -> str:
    text = f"{value:.{dec}f}"
    if abs(float(text) - value) > 10 ** (-dec) / 1000:
        raise DriverError(f"{what}: the controller accepts {dec} decimal(s); {value!r} would become {text}")
    return text
