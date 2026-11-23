"""
Applikon ez2-Control driver (writes only the allow-listed addresses, see WRITABLE_ADDRESSES).

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

Addresses used here are identical on R1-sx and R2-dx (controller IPs: local.env, not in the repository).
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

from .base import (ManualState, PH_MODE_PARAMS, ControllerDriver, ControllerSnapshot, DriverCapabilities, DriverError,
                   LoopActuator, LoopConfig, LoopInfo, PhControl)

log = logging.getLogger(__name__)

# Sensor indices (second level of 1.x.<sensor>)
PH, DO2, TEMPERATURE, STIRRER = 0, 1, 2, 13
LEVEL, REDOX = 3, 32
BASE_ACTUATOR = 29
ACID_ACTUATOR, ANTIFOAM_ACTUATOR = 28, 30     # "Addition 2" (pH high when assigned to pH), "Antifoam"
# Actuators (second level of 2.x.<actuator>), as configured on both units
N2_VALVE, AIR_VALVE, STIRRER_ACT, HEATING, COOLING = 0, 1, 18, 19, 46

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
    "ph_deadband_enabled":      f"1.29.{PH}",
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
    # optional channels (stored in snapshot.extra)
    "do2":                      f"1.15.{DO2}",
    "do2_setpoint":             f"1.13.{DO2}",
    "do2_high_limit":           f"1.4.{DO2}",
    "do2_low_limit":            f"1.5.{DO2}",
    "heating_out":              f"2.12.{HEATING}",
    "heating_factor":           f"2.4.{HEATING}",
    "cooling_out":              f"2.12.{COOLING}",
    "cooling_factor":           f"2.4.{COOLING}",
    "stirrer_out_rpm":          f"2.12.{STIRRER_ACT}",
    "n2_out":                   f"2.12.{N2_VALVE}",
    "n2_factor":                f"2.4.{N2_VALVE}",
    "air_out":                  f"2.12.{AIR_VALVE}",
    "air_factor":               f"2.4.{AIR_VALVE}",
    # temperature loop (same structure as pH)
    "temperature_control_type": f"1.19.{TEMPERATURE}",
    "temperature_pid_p":        f"3.0.{TEMPERATURE}",
    "temperature_pid_i":        f"3.3.{TEMPERATURE}",
    "temperature_pid_d":        f"3.6.{TEMPERATURE}",
    "temperature_deadband":     f"3.9.{TEMPERATURE}",
    "temperature_deadband_enabled": f"1.29.{TEMPERATURE}",
    "temperature_cycle_time":   f"3.12.{TEMPERATURE}",
    "temperature_settling_time": f"3.71.{TEMPERATURE}",
    # Addition 2 (acid when on the pH loop's high side), level/antifoam, redox
    "acid_cumulative_ml":       f"2.13.{ACID_ACTUATOR}",
    "acid_flow":                f"2.12.{ACID_ACTUATOR}",
    "acid_factor":              f"2.4.{ACID_ACTUATOR}",
    "level":                    f"1.15.{LEVEL}",
    "antifoam_cumulative_ml":   f"2.13.{ANTIFOAM_ACTUATOR}",
    "antifoam_out":             f"2.12.{ANTIFOAM_ACTUATOR}",
    "antifoam_factor":          f"2.4.{ANTIFOAM_ACTUATOR}",
    "redox":                    f"1.15.{REDOX}",
    # loop on/off
    "loop_ph":                  f"1.14.{PH}",
    "loop_temperature":         f"1.14.{TEMPERATURE}",
    "loop_stirrer":             f"1.14.{STIRRER}",
    "loop_do2":                 f"1.14.{DO2}",
    "loop_level":               f"1.14.{LEVEL}",
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

# Loops whose type and parameters a profile may load: pH and temperature (same addresses,
# different sensor index).
CONTROL_SENSORS = {"ph": PH, "temperature": TEMPERATURE}
LOOP_TYPE_ADDRESSES = {loop: f"1.19.{s}" for loop, s in CONTROL_SENSORS.items()}
LOOP_PARAM_ADDRESSES = {loop: {"P": f"3.0.{s}", "I": f"3.3.{s}", "D": f"3.6.{s}", "deadband": f"3.9.{s}",
                               "cycle_time": f"3.12.{s}", "settling_time": f"3.71.{s}"}
                        for loop, s in CONTROL_SENSORS.items()}
LOOP_PARAM_LIMITS = {loop: {"P": (f"3.2.{s}", f"3.1.{s}"), "I": (f"3.5.{s}", f"3.4.{s}"),
                            "D": (f"3.8.{s}", f"3.7.{s}"), "deadband": (f"3.11.{s}", f"3.10.{s}"),
                            "cycle_time": (f"3.14.{s}", f"3.13.{s}")}
                     for loop, s in CONTROL_SENSORS.items()}

# Process (alarm) limits: 1.4 = high, 1.5 = low. They only raise alarms/notifications;
# the interlock limits that act on actuators (1.20 / 1.21) are NOT writable.
LIMIT_SENSORS = {"ph": PH, "temperature": TEMPERATURE}
LIMIT_ADDRESSES = frozenset(f"1.{k}.{s}" for s in LIMIT_SENSORS.values() for k in (4, 5))

# Loop on/off ("Start/Stop selected loops"): 1.14.<sensor>, values 0/1 only, exactly what the
# controller's own Start/Stop buttons write (rightpanelsensors.js -> senData.active()).
SWITCH_SENSORS = {"ph": PH, "temperature": TEMPERATURE, "stirrer": STIRRER, "do2": DO2, "level": LEVEL}
LOOP_ACTIVE_ADDRESSES = {loop: f"1.14.{s}" for loop, s in SWITCH_SENSORS.items()}

# Dose counter reset: writing "1" to 2.13.<actuator> resets that counter to 0. It is what the
# controller's own "Reset Dose Monitor" / "Reset All Dose Monitors" buttons do
# (dosesettings.js and rightpanelactuators.js -> actuatorData.doseValue(id, 1)); any login level.
# Only "1" is accepted: the counter value itself can never be written.
DOSE_RESET_ADDRESSES = {"base": f"2.13.{BASE_ACTUATOR}", "acid": f"2.13.{ACID_ACTUATOR}",
                        "antifoam": f"2.13.{ANTIFOAM_ACTUATOR}"}

# 2.3: manual drive of the pumps (prime 2.46, 0/1 only), loop configuration commands (Engineer, loop
# off; 3.51 add above / 3.52 add below / 3.53 remove, value = actuator id) and dO2 / stirrer values.
PUMP_IDS = tuple(range(22, 31))
PRIME_ADDRESSES = frozenset(f"2.46.{a}" for a in PUMP_IDS)
ASSIGN_ADDRESSES = frozenset(f"3.{k}.{s}" for k in (51, 52, 53) for s in (PH, DO2, TEMPERATURE, STIRRER, LEVEL))
CHANNEL_SENSORS = {"do2": DO2, "stirrer": STIRRER}
CHANNEL_ADDRESSES = frozenset([f"1.13.{DO2}"] + [f"1.{k}.{s}" for k in (4, 5) for s in CHANNEL_SENSORS.values()])
CASCADE_FLAG = 0x40000000

WRITABLE_ADDRESSES = (SETPOINT_ADDRESSES | PRIME_ADDRESSES | ASSIGN_ADDRESSES | CHANNEL_ADDRESSES | frozenset(LOOP_TYPE_ADDRESSES.values())
                      | frozenset(LOOP_ACTIVE_ADDRESSES.values())
                      | frozenset(DOSE_RESET_ADDRESSES.values())
                      | frozenset(a for d in LOOP_PARAM_ADDRESSES.values() for a in d.values())
                      | LIMIT_ADDRESSES)


def build_write_request(address: str, text: str) -> bytes:
    if address not in WRITABLE_ADDRESSES:
        raise DriverError(f"refusing to write {address}: not an address FM2 may write")
    if not text or any(c in text for c in "\rJSs|"):
        raise DriverError(f"refusing to write malformed value {text!r}")
    if address in DOSE_RESET_ADDRESSES.values() and text != "1":
        raise DriverError(f"refusing to write {text!r} to the dose counter {address}: only the reset command 1")
    if address in PRIME_ADDRESSES and text not in ("0", "1"):
        raise DriverError(f"refusing to write {text!r} to the prime command {address}: only 0 or 1")
    if address in ASSIGN_ADDRESSES and not text.isdigit():
        raise DriverError(f"refusing to write {text!r} to the loop configuration {address}: only an actuator id")
    if address in LOOP_ACTIVE_ADDRESSES.values() and text not in ("0", "1"):
        raise DriverError(f"refusing to write {text!r} to the loop switch {address}: only 0 or 1")
    if address in LOOP_TYPE_ADDRESSES.values() and text not in WRITABLE_PH_MODES.values():
        raise DriverError(f"refusing to set control type {text!r} at {address}: only PID or Adaptive")
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

    def control(loop: str) -> PhControl:
        ctype = f(f"{loop}_control_type")
        mode = CONTROL_TYPES.get(int(ctype), f"type-{int(ctype)}") if ctype is not None else None
        pid = {"P": f(f"{loop}_pid_p"), "I": f(f"{loop}_pid_i"), "D": f(f"{loop}_pid_d"),
               "deadband": f(f"{loop}_deadband"), "cycle_time": f(f"{loop}_cycle_time"),
               "settling_time": f(f"{loop}_settling_time")}
        db_flag = f(f"{loop}_deadband_enabled")
        db_enabled = None if db_flag is None else db_flag == 1
        if db_enabled is False:      # not shown nor used by the controller: not reported
            pid.pop("deadband")
        return PhControl(mode=mode, pid={k: v for k, v in pid.items() if v is not None} or None,
                         deadband_enabled=db_enabled)

    def pct(out_key: str, factor_key: str):
        v, fac = f(out_key), f(factor_key)
        return v / fac * 100.0 if (v is not None and fac) else None

    extra = {
        "do2": f("do2"), "do2_setpoint": f("do2_setpoint"),
        "do2_low_limit": f("do2_low_limit"), "do2_high_limit": f("do2_high_limit"),
        "heating_pct": pct("heating_out", "heating_factor"), "cooling_pct": pct("cooling_out", "cooling_factor"),
        "stirrer_out_rpm": f("stirrer_out_rpm"),
        "n2_pct": pct("n2_out", "n2_factor"), "air_pct": pct("air_out", "air_factor"),
        # mL only when the pump is calibrated (factor > 0); otherwise the counter is not a volume
        "acid_cumulative_ml": f("acid_cumulative_ml") if f("acid_factor") else None,
        "acid_pump_pct": pct("acid_flow", "acid_factor"),
        "level_contact": {"CONTACT": 1.0, "NO CONT": 0.0}.get((values.get("level") or "").strip()),
        "antifoam_cumulative_ml": f("antifoam_cumulative_ml") if f("antifoam_factor") else None,
        "antifoam_pct": pct("antifoam_out", "antifoam_factor"),
        "redox_mv": f("redox"),
    }

    return ControllerSnapshot(
        ts=ts,
        ph=f("ph"), ph_setpoint=f("ph_setpoint"),
        temperature=f("temperature"), temperature_setpoint=f("temperature_setpoint"),
        stirrer=f("stirrer"), stirrer_setpoint=f("stirrer_setpoint"),
        base_cumulative_ml=f("base_cumulative_ml"), base_pump_pct=pump_pct,
        ph_low_limit=f("ph_low_limit"), ph_high_limit=f("ph_high_limit"),
        temperature_low_limit=f("temperature_low_limit"),
        temperature_high_limit=f("temperature_high_limit"),
        ph_control=control("ph"), temperature_control=control("temperature"),
        loops_active={k: f(f"loop_{k}") == 1 for k in ("ph", "temperature", "stirrer", "do2", "level")
                      if f(f"loop_{k}") is not None},
        extra={k: v for k, v in extra.items() if v is not None},
    )


class Ez2ControlDriver(ControllerDriver):
    """Reads one ez2-Control over its WebSocket. Keeps one connection open, reconnects on failure."""

    capabilities = DriverCapabilities(read_values=True, read_limits=True,
                                      write_setpoints=True, write_ph_control=True, write_limits=True)

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
    def _logged_in_writes(self, writes: list[tuple[str, str, int]], wait_readback: bool = True, as_level: Optional[int] = None) -> None:
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
                ws.send(build_login_request(as_level or self.login_level, self.login_user, password))
            except Exception as e:
                raise DriverError(f"{self.url}: login failed: {e}") from e
            level, until = None, time.monotonic() + 2.0
            while time.monotonic() < until:
                state = self._query_once({"login": LOGIN_ADDRESS}).get("login", "")
                level = to_float(state.split("|")[0]) if state else None
                if level is not None and (level >= 1 or level < 0):
                    break
                time.sleep(0.2)
            if level is not None and level < 0:
                # verified 23 Nov 2026: -5 while someone is logged in on the display / web UI
                raise DriverError("controller locked: someone is logged in on the controller's display or web UI "
                                  f"(code {int(level)}). Log out there (View) and retry. Nothing was written.")
            if level is None or level < 1:
                raise DriverError("login refused by the controller (wrong password, or another user is "
                                  "logged in on the display/web UI). Nothing was written.")
            logged_in = True
            for address, text, dec, frame in frames:
                try:
                    self._ws.send(frame)
                except Exception as e:
                    raise DriverError(f"{self.url}: write of {address} failed: {e}") from e
                log.info("%s: %s -> %s sent", self.reactor_id, address, text)
                if not wait_readback:
                    time.sleep(0.15)
                    continue
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

    def write_limits(self, parameter: str, low: Optional[float], high: Optional[float]) -> None:
        """
        Write the process (alarm) limits of pH or temperature, in one login session. Checked
        against the sensor range and display decimals; low must stay below high (also against
        the limit left unchanged). Only limits that differ are written.
        """
        import math
        if parameter not in LIMIT_SENSORS:
            raise DriverError(f"process limits can be written for pH and temperature only, not {parameter!r}")
        for v in (low, high):
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
                raise DriverError(f"invalid limit {v!r}")
        sensor = LIMIT_SENSORS[parameter]
        a_hi, a_lo = f"1.4.{sensor}", f"1.5.{sensor}"
        with self._lock:
            cur = self._query({"hi": a_hi, "lo": a_lo, "max": f"1.6.{sensor}", "min": f"1.7.{sensor}",
                               "decimals": f"1.58.{sensor}"})
            rmin, rmax, dec = to_float(cur.get("min")), to_float(cur.get("max")), to_float(cur.get("decimals"))
            cur_lo, cur_hi = to_float(cur.get("lo")), to_float(cur.get("hi"))
            if None in (rmin, rmax, dec, cur_lo, cur_hi):
                raise DriverError(f"{parameter}: cannot read the current limits/range, not writing")
            dec = int(dec)
            new_lo = cur_lo if low is None else low
            new_hi = cur_hi if high is None else high
            for name, v in (("low", new_lo), ("high", new_hi)):
                if not (rmin <= v <= rmax):
                    raise DriverError(f"{parameter} {name} limit {v:g} outside the sensor range {rmin:g}–{rmax:g}")
            if new_lo >= new_hi:
                raise DriverError(f"{parameter}: low limit {new_lo:g} must be below high limit {new_hi:g}")
            writes = []
            for addr, v, old in ((a_lo, low, cur_lo), (a_hi, high, cur_hi)):
                if v is None:
                    continue
                text = _format(v, dec, f"{parameter} limit")
                if abs(old - float(text)) >= 10 ** (-dec) / 2:
                    writes.append((addr, text, dec))
            # order matters when the range moves past the old one: never let low >= high in between
            if len(writes) == 2 and new_lo >= cur_hi:
                writes.reverse()
            if writes:
                self._logged_in_writes(writes)

    def write_ph_control(self, control: PhControl) -> None:
        self.write_loop_control("ph", control)

    def write_loop_control(self, loop: str, control: PhControl) -> None:
        """
        Load the control type (PID or Adaptive) and loop parameters of the pH or temperature
        loop — from planning only (services/commands.load_loop_control enforces it). PID: P, I,
        D, dead band, cycle time. Adaptive: the same with P/I/D as starting values, plus settling
        time. Checked against the controller's own min/max; only values that differ are written,
        in one login session.
        """
        import math
        if loop not in CONTROL_SENSORS:
            raise DriverError(f"loop parameters can be loaded for pH and temperature only, not {loop!r}")
        if control is None or control.mode not in WRITABLE_PH_MODES:
            raise DriverError(f"{loop} control mode must be one of {sorted(WRITABLE_PH_MODES)}, got "
                              f"{getattr(control, 'mode', None)!r}")
        addresses, limits = LOOP_PARAM_ADDRESSES[loop], LOOP_PARAM_LIMITS[loop]
        params = {k: v for k, v in (control.pid or {}).items() if v is not None}
        unknown = set(params) - set(addresses)
        if unknown:
            raise DriverError(f"unknown {loop} loop parameters {sorted(unknown)}: expected {sorted(addresses)}")
        params = {k: v for k, v in params.items() if k in PH_MODE_PARAMS[control.mode]}
        with self._lock:
            if "deadband" in params:
                # The controller only uses a dead band where it is enabled (1.29.<sensor>, read only);
                # elsewhere the value is not shown on the display and is not written.
                flag = to_float(self._query({"db": f"1.29.{CONTROL_SENSORS[loop]}"}).get("db"))
                if flag != 1:
                    log.info("%s %s loop: dead band not enabled on the controller, not written", self.reactor_id, loop)
                    params.pop("deadband")
            keys = {"type": LOOP_TYPE_ADDRESSES[loop]}
            for term in params:
                keys[term] = addresses[term]
                if term in limits:
                    keys[term + "_min"], keys[term + "_max"] = limits[term]
            cur = self._query(keys)
            writes: list[tuple[str, str, int]] = []
            want_type = WRITABLE_PH_MODES[control.mode]
            if to_float(cur.get("type")) != float(want_type):
                writes.append((LOOP_TYPE_ADDRESSES[loop], want_type, 0))
            for term, value in params.items():
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise DriverError(f"{loop} {term}: invalid value {value!r}")
                if term in limits:
                    lo, hi = to_float(cur.get(term + "_min")), to_float(cur.get(term + "_max"))
                else:
                    lo, hi = SETTLING_TIME_RANGE
                if lo is None or hi is None:
                    raise DriverError(f"{loop} {term}: cannot read the controller's range, not writing")
                if not (lo <= value <= hi):
                    raise DriverError(f"{loop} {term} {value:g} outside the controller's range {lo:g}–{hi:g}")
                raw = cur.get(term, "")
                if to_float(raw) is None:
                    raise DriverError(f"{loop} {term}: cannot read the current value, not writing")
                dec = len(raw.split(".")[1]) if "." in raw else 0   # controller's display decimals
                text = _format(value, dec, f"{loop} {term}")
                if abs(to_float(raw) - float(text)) >= 10 ** (-dec) / 2:
                    writes.append((addresses[term], text, dec))
            if writes:
                self._logged_in_writes(writes)

    def write_loop_active(self, loop: str, on: bool) -> None:
        """Start (1) or stop (0) one control loop, like the controller's own Start/Stop buttons."""
        if loop not in LOOP_ACTIVE_ADDRESSES:
            raise DriverError(f"cannot switch the {loop!r} loop")
        with self._lock:
            self._logged_in_writes([(LOOP_ACTIVE_ADDRESSES[loop], "1" if on else "0", 0)])

    def reset_dose_counter(self, counter: str) -> None:
        """Reset one dose counter to 0 (the controller's "Reset Dose Monitor")."""
        if counter not in DOSE_RESET_ADDRESSES:
            raise DriverError(f"cannot reset the {counter!r} counter")
        with self._lock:
            self._logged_in_writes([(DOSE_RESET_ADDRESSES[counter], "1", 0)])

    # -- manual pumps (prime), loop assignments, dO2/stirrer values (2.3) ------------
    def manual_state(self, pump_id: int) -> ManualState:
        if pump_id not in PUMP_IDS:
            raise DriverError(f"actuator {pump_id} is not a pump")
        with self._lock:
            v = self._query({"name": f"2.1.{pump_id}", "assigned": f"2.7.{pump_id}", "controlled": f"2.19.{pump_id}",
                             "manual": f"2.20.{pump_id}", "inter": f"2.47.{pump_id}", "prime": f"2.46.{pump_id}",
                             "count": f"2.13.{pump_id}"})
            sensor = to_float(v.get("assigned"))
            loop_on = False
            if sensor is not None and sensor >= 0:
                loop_on = to_float(self._query({"a": f"1.14.{int(sensor)}"}).get("a")) == 1
        return ManualState(pump_id=pump_id, name=(v.get("name") or f"Pump {pump_id}").strip(), assigned_loop_on=loop_on,
                           controlled=to_float(v.get("controlled")) == 1, interlocked=to_float(v.get("inter")) == 1,
                           manual_on=(to_float(v.get("manual")) or 0) != 0, prime_on=to_float(v.get("prime")) == 1,
                           counter_ml=to_float(v.get("count")))

    def write_prime(self, pump_id: int, on: bool) -> None:
        if pump_id not in PUMP_IDS:
            raise DriverError(f"actuator {pump_id} is not a pump")
        with self._lock:
            # Operator level is enough (verified 23 Nov 2026)
            self._logged_in_writes([(f"2.46.{pump_id}", "1" if on else "0", 0)], as_level=1)

    def write_loop_assignment(self, loop: str, actuator_id: int, side: int, cascade: bool = False) -> None:
        sensor = dict(CONFIG_LOOPS).get(loop)
        if sensor is None or loop == "redox":
            raise DriverError(f"unknown loop {loop!r}")
        if side not in (-1, 0, 1) or not (0 <= actuator_id < N_ACTUATORS):
            raise DriverError("invalid actuator or side")
        # loopconfiguration.js: 3.51 = "positive" side (position > 0, acts below the setpoint, e.g. Alkali),
        # 3.52 = "negative" side (above the setpoint, e.g. Addition 2 as acid), 3.53 = remove
        code = {1: 51, -1: 52, 0: 53}[side]
        value = actuator_id + (CASCADE_FLAG if cascade else 0)
        with self._lock:
            if to_float(self._query({"a": f"1.14.{sensor}"}).get("a")) == 1:
                raise DriverError(f"the {loop} loop is on: stop it before changing its actuators")
            self._logged_in_writes([(f"3.{code}.{sensor}", str(value), 0)], wait_readback=False, as_level=2)

    def write_channel_values(self, channel: str, setpoint: Optional[float], low: Optional[float], high: Optional[float]) -> dict:
        import math
        sensor = CHANNEL_SENSORS.get(channel)
        if sensor is None:
            raise DriverError(f"no channel values for {channel!r}")
        if channel == "stirrer" and setpoint is not None:
            raise DriverError("the stirrer setpoint is written as a setpoint, not here")
        with self._lock:
            cur = self._query({"sp": f"1.13.{sensor}", "hi": f"1.4.{sensor}", "lo": f"1.5.{sensor}", "max": f"1.6.{sensor}",
                               "min": f"1.7.{sensor}", "dec": f"1.58.{sensor}"})
            rmin, rmax, dec = to_float(cur.get("min")), to_float(cur.get("max")), to_float(cur.get("dec"))
            if None in (rmin, rmax, dec):
                raise DriverError(f"{channel}: cannot read the controller's range, not writing")
            dec = int(dec)
            lo_new = to_float(cur.get("lo")) if low is None else low
            hi_new = to_float(cur.get("hi")) if high is None else high
            for name, val in (("setpoint", setpoint), ("low limit", low), ("high limit", high)):
                if val is not None and (not math.isfinite(val) or not (rmin <= val <= rmax)):
                    raise DriverError(f"{channel} {name} {val!r} outside the controller's range {rmin:g}–{rmax:g}")
            if lo_new is not None and hi_new is not None and lo_new >= hi_new:
                raise DriverError(f"{channel}: low limit {lo_new:g} must be below high limit {hi_new:g}")
            writes = []
            if setpoint is not None:
                writes.append((f"1.13.{sensor}", _format(setpoint, dec, f"{channel} setpoint"), dec))
            lim = [(f"1.5.{sensor}", low), (f"1.4.{sensor}", high)]
            if low is not None and to_float(cur.get("hi")) is not None and low >= to_float(cur.get("hi")):
                lim.reverse()
            writes += [(a, _format(val, dec, f"{channel} limit"), dec) for a, val in lim if val is not None]
            if writes:
                self._logged_in_writes(writes)
            after = self._query({"setpoint": f"1.13.{sensor}", "high": f"1.4.{sensor}", "low": f"1.5.{sensor}"})
        return {k: to_float(x) for k, x in after.items()}

    # -- loop configuration (read only) ----------------------------------------------
    def read_loop_config(self) -> LoopConfig:
        """Read the loops, the actuators assigned to them, cascades and calibration (read only)."""
        keys: dict[str, str] = {}
        for _, s in CONFIG_LOOPS:
            for name, p in SENSOR_FIELDS.items():
                keys[f"s{s}.{name}"] = f"1.{p}.{s}"
        for a in range(N_ACTUATORS):
            for name, p in ACTUATOR_FIELDS.items():
                keys[f"a{a}.{name}"] = f"2.{p}.{a}"
        keys["level.type"], keys["level.sens"] = f"5.{LEVEL}.0", f"5.{LEVEL}.1"
        for s in range(N_SENSORS):
            keys.setdefault(f"s{s}.name", f"1.1.{s}")
            keys.setdefault(f"s{s}.available", f"1.11.{s}")
        values: dict[str, str] = {}
        items = list(keys.items())
        with self._lock:
            for i in range(0, len(items), 40):
                try:
                    values.update(self._query(dict(items[i:i + 40])))
                except DriverError:
                    if not values:
                        raise
        if not values:
            raise DriverError(f"{self.url}: no answer for the loop configuration")
        return decode_loop_config(values)


# --- loop configuration: addresses and decoding (names from the web UI's sensordata.js /
#     actuatordata.js, see docs/controller-inspection.md) ---------------------------------
CONFIG_LOOPS = (("ph", PH), ("temperature", TEMPERATURE), ("stirrer", STIRRER), ("do2", DO2),
                ("level", LEVEL), ("redox", REDOX))
SENSOR_FIELDS = {"name": 1, "available": 11, "setpoint": 13, "active": 14, "value": 15, "in_loop": 16,
                 "type": 19, "assigned": 52, "position": 53}
ACTUATOR_FIELDS = {"name": 1, "dose_mon": 3, "factor": 4, "unit": 5, "assigned": 7, "position": 8,
                   "available": 21}
N_ACTUATORS = 64
N_SENSORS = 40
PUMP_ACTUATORS = range(22, 31)      # stepper pumps 22-27, digital pumps 28-30


def decode_loop_config(v: dict[str, str]) -> LoopConfig:
    def num(k: str) -> Optional[float]:
        return to_float(v.get(k))

    def i(k: str, default: int = -1) -> int:
        x = num(k)
        return int(x) if x is not None else default

    acts: list[tuple[int, LoopActuator]] = []    # (sensor it is assigned to, actuator)
    unassigned, free, absent = [], [], []
    for a in range(N_ACTUATORS):
        avail = i(f"a{a}.available", 0)
        if avail == 1:
            absent.append(("output", a, (v.get(f"a{a}.name") or f"Output {a}").strip()))
        if avail < 3:
            continue
        pump = a in PUMP_ACTUATORS
        factor = num(f"a{a}.factor")
        unit = (v.get(f"a{a}.unit") or "").strip()
        act = LoopActuator(id=a, name=(v.get(f"a{a}.name") or f"Actuator {a}").strip(), side=i(f"a{a}.position", 0),
                           pump=pump,
                           calibrated=(bool(factor) and unit.lower().startswith("ml")) if pump else None,
                           dose_monitoring=(i(f"a{a}.dose_mon", 0) == 1) if pump else None, unit=unit)
        sensor = i(f"a{a}.assigned")
        if sensor >= 0:
            acts.append((sensor, act))
        else:
            free.append(act)
            if pump:
                unassigned.append(act)
    key_of = {s: k for k, s in CONFIG_LOOPS}
    loops = []
    for key, s in CONFIG_LOOPS:
        own = [a for sensor, a in acts if sensor == s]
        # other sensors used as this loop's actuator (cascade), e.g. the stirrer on the dO2 loop
        for k2, s2 in CONFIG_LOOPS:
            if s2 != s and i(f"s{s2}.assigned") == s:
                own.append(LoopActuator(id=s2, name=(v.get(f"s{s2}.name") or k2).strip(),
                                        side=i(f"s{s2}.position", 0), kind="sensor"))
        own.sort(key=lambda a: (a.side < 0, abs(a.side)))
        drives = key_of.get(i(f"s{s}.assigned"))
        probe = None
        if key == "level" and num("level.type") is not None:
            probe = f"{'Foam' if i('level.type') == 1 else 'Level'} probe, sensitivity {'High' if i('level.sens') == 1 else 'Low'}"
        ctype = num(f"s{s}.type")
        loops.append(LoopInfo(
            key=key, sensor=s, name=(v.get(f"s{s}.name") or key).strip(), available=i(f"s{s}.available", 0),
            active=None if num(f"s{s}.active") is None else i(f"s{s}.active") == 1,
            control_type=CONTROL_TYPES.get(int(ctype)) if ctype is not None and ctype >= 0 else None,
            value=(v.get(f"s{s}.value") or "").strip() or None, setpoint=num(f"s{s}.setpoint"),
            has_actuator=None if num(f"s{s}.in_loop") is None else i(f"s{s}.in_loop") == 1,
            actuators=tuple(own), drives=drives, probe=probe))
    loop_sensors = {s for _, s in CONFIG_LOOPS}
    for s in range(N_SENSORS):
        if s not in loop_sensors and i(f"s{s}.available", 0) == 1:
            absent.append(("input", s, (v.get(f"s{s}.name") or f"Input {s}").strip()))
    return LoopConfig(loops=tuple(loops), unassigned_pumps=tuple(unassigned), free_outputs=tuple(free), not_installed=tuple(absent))


def _format(value: float, dec: int, what: str) -> str:
    text = f"{value:.{dec}f}"
    if abs(float(text) - value) > 10 ** (-dec) / 1000:
        raise DriverError(f"{what}: the controller accepts {dec} decimal(s); {value!r} would become {text}")
    return text
