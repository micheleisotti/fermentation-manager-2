"""ez2-Control driver against a fake controller speaking the same WebSocket protocol."""

import base64
import threading

import pytest
from websockets.sync.server import serve

from fm2.drivers import DriverError, make_driver
from fm2.drivers.ez2control import (READ_MAP, SETPOINT_ADDRESSES, build_read_request, build_write_request,
                                    parse_frame, to_float)

# Values read from R2-dx on 2026-11-09 (no culture, probes disconnected), plus a pump run.
R2_VALUES = {
    "1.15.0": "6.90", "1.13.0": "6.20", "1.4.0": "6.50", "1.5.0": "6.00", "1.19.0": "0",
    "3.0.0": "100.00", "3.3.0": "2.00", "3.6.0": "10.00",
    "1.15.2": "22.7", "1.13.2": "37.0", "1.4.2": "38.5", "1.5.2": "5.0",
    "1.15.13": "0", "1.13.13": "400",
    "2.1.29": "Alkali", "2.13.29": "1.61", "2.12.29": "4.80", "2.4.29": "4.80",
    "1.15.3": "NO CONT", "1.4.3": "1.0E+05",
    # sensor range (max, min) and display decimals, used to validate writes
    "1.6.0": "14.00", "1.7.0": "0.00", "1.58.0": "2",
    "1.6.2": "60.0", "1.7.2": "0.0", "1.58.2": "1",
    "1.6.13": "2000", "1.7.13": "0", "1.58.13": "0",
    # pH PID term ranges (max, min) — fake values for the tests
    "3.1.0": "1000.00", "3.2.0": "0.00", "3.4.0": "10000.00", "3.5.0": "0.00", "3.7.0": "1000.00", "3.8.0": "0.00",
}


class FakeController:
    """Answers J<addr>S requests; records every request; splits answers over two frames.
    With drop_after_answer=True it kills the TCP connection after each answer without a
    close frame, like the real controller does with idle connections."""

    def __init__(self, values, drop_after_answer=False, password="fake-test-pw"):
        self.values = dict(values)
        self.password = password
        self.holder = None          # connection currently logged in (one login at a time)
        self.other_user_logged_in = False
        self.drop_after_answer = drop_after_answer
        self.connections = 0
        self.requests: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.ignored_writes: list[tuple[str, str]] = []
        self.logins: list[str] = []
        self.server = serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self, ws):
        self.connections += 1
        me = object()
        level = lambda: 2 if self.holder is me else 0  # noqa: E731
        for msg in ws:
            text = msg.decode("ascii") if isinstance(msg, bytes) else msg
            lines = [l for l in text.split("\r") if l]
            self.requests.extend(lines)
            answers = []
            for l in lines:
                assert l.startswith("J"), f"malformed request: {l!r}"
                if "s" in l:                       # write: J<addr>s<value>
                    addr, val = l[1:].split("s", 1)
                    if addr == "0.11":             # login / logout
                        self.logins.append(val.split("|")[0])
                        lvl, _user, token = val.split("|")
                        if lvl == "0":
                            if self.holder is me:
                                self.holder = None
                        elif (not self.other_user_logged_in and self.holder is None
                              and base64.b64decode(token).decode() == self.password):
                            self.holder = me
                        continue
                    if level() >= 1:               # writes need a login on this connection
                        self.writes.append((addr, val))
                        self.values[addr] = val
                    else:
                        self.ignored_writes.append((addr, val))
                    continue
                assert l.endswith("S"), f"unexpected request: {l!r}"
                addr = l[1:-1]
                if addr == "0.11":
                    answers.append(f"J0.11A{level()}|{0 if level() else -1}|\r")
                elif addr in self.values:
                    answers.append(f"J{addr}A{self.values[addr]}\r")
            half = len(answers) // 2
            for chunk in (answers[:half], answers[half:]):
                if chunk:
                    ws.send("".join(chunk).encode("ascii"))
            if self.drop_after_answer:
                ws.socket.close()  # abrupt: no WebSocket close frame
                return

    def stop(self):
        self.server.shutdown()


@pytest.fixture(autouse=True)
def password_file(tmp_path, monkeypatch):
    f = tmp_path / "ez2-password.txt"
    f.write_text("fake-test-pw\n")
    monkeypatch.setenv("FM2_EZ2_PASSWORD_FILE", str(f))
    return f


@pytest.fixture
def fake():
    f = FakeController(R2_VALUES)
    yield f
    f.stop()


def test_protocol_helpers():
    assert build_read_request(["1.15.0", "2.13.29"]) == b"J1.15.0S\rJ2.13.29S\r"
    assert parse_frame(b"J1.15.0A6.90\rJ1.4.3A1.0E+05\rJ9.9E12\r") == {"1.15.0": "6.90", "1.4.3": "1.0E+05"}
    assert to_float("NO CONT") is None and to_float(" 22.7") == 22.7 and to_float("1.0E+05") == 1e5


def test_read_snapshot(fake):
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    snap = drv.read()
    assert snap.ph == 6.90 and snap.ph_setpoint == 6.20
    assert (snap.ph_low_limit, snap.ph_high_limit) == (6.00, 6.50)
    assert snap.temperature == 22.7 and snap.temperature_setpoint == 37.0
    assert (snap.temperature_low_limit, snap.temperature_high_limit) == (5.0, 38.5)
    assert snap.stirrer == 0 and snap.stirrer_setpoint == 400
    assert snap.base_cumulative_ml == 1.61
    assert snap.base_pump_pct == pytest.approx(100.0)
    assert snap.ph_control.mode == "pid"
    assert snap.ph_control.pid == {"P": 100.0, "I": 2.0, "D": 10.0}
    # second read reuses the connection
    fake.values["2.13.29"] = "1.75"
    assert drv.read().base_cumulative_ml == 1.75
    drv.close()


def test_ph_control_switch_to_adaptive(fake):
    from fm2.drivers import PhControl
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    drv.write_ph_control(PhControl(mode="adaptive", pid={"P": 5.0, "I": 1.0, "D": 0.0}))
    assert fake.writes == [("1.19.0", "1")]                 # PID terms ignored in Adaptive
    assert drv.read().ph_control.mode == "adaptive"
    assert fake.holder is None
    drv.close()


def test_ph_control_pid_writes_only_differences(fake):
    from fm2.drivers import PhControl
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    # controller is already PID with P 100 / I 2 / D 10: only I changes
    drv.write_ph_control(PhControl(mode="pid", pid={"P": 100.0, "I": 2.5, "D": 10.0}))
    assert fake.writes == [("3.3.0", "2.50")]
    assert drv.read().ph_control.pid == {"P": 100.0, "I": 2.5, "D": 10.0}
    fake.writes.clear(); fake.logins.clear()
    drv.write_ph_control(PhControl(mode="pid", pid={"P": 100.0, "I": 2.5, "D": 10.0}))
    assert fake.writes == [] and fake.logins == []          # nothing to do: no login at all
    drv.close()


@pytest.mark.parametrize("control", [
    {"mode": "external"}, {"mode": "p-adaptive"}, {"mode": None},
    {"mode": "pid", "pid": {"P": 2000.0}},                 # above the controller's range
    {"mode": "pid", "pid": {"P": -1.0}},
    {"mode": "pid", "pid": {"P": 1.234}},                  # more decimals than the controller shows
    {"mode": "pid", "pid": {"Kp": 1.0}},                   # unknown term
])
def test_ph_control_rejected_without_sending(fake, control):
    from fm2.drivers import PhControl
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(DriverError):
        drv.write_ph_control(PhControl(**control))
    assert fake.writes == [] and fake.logins == []
    drv.close()


def test_load_ph_control_from_planning_end_to_end(fake):
    from fm2.drivers import PhControl
    from fm2.services.commands import CommandRejected, load_ph_control
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(CommandRejected):
        load_ph_control(drv, PhControl("adaptive", None), "fermenting")   # never after Start
    assert fake.writes == []
    load_ph_control(drv, PhControl("pid", {"P": 80.0, "I": 2.0, "D": 10.0}), "planned")
    assert fake.writes == [("3.0.0", "80.00")]
    drv.close()


def test_read_never_writes(fake):
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    drv.read()
    drv.read()
    assert fake.requests and all(r.endswith("S") and "s" not in r for r in fake.requests)
    assert set(r[1:-1] for r in fake.requests) == set(READ_MAP.values())
    assert fake.writes == []
    with pytest.raises(DriverError):
        drv.write_ph_control(None)          # pH mode/PID stay controller-only for now
    assert fake.writes == []
    drv.close()


def test_write_setpoints(fake):
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    drv.write_setpoint("ph", 6.3)
    drv.write_setpoint("temperature", 30)
    drv.write_setpoint("stirrer", 250)
    assert fake.writes == [("1.13.0", "6.30"), ("1.13.2", "30.0"), ("1.13.13", "250")]
    assert fake.logins == ["2", "0"] * 3      # login + logout around every write
    assert fake.holder is None                 # nobody left logged in
    snap = drv.read()
    assert (snap.ph_setpoint, snap.temperature_setpoint, snap.stirrer_setpoint) == (6.3, 30.0, 250.0)
    drv.close()


@pytest.mark.parametrize("parameter,value", [
    ("ph", 14.5),            # above the controller's range
    ("temperature", -1),     # below
    ("ph", 6.255),           # more decimals than the controller accepts
    ("stirrer", 250.5),
    ("ph", float("nan")),
    ("foam", 1.0),           # not a setpoint we may write
])
def test_write_rejected_without_sending(fake, parameter, value):
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(DriverError):
        drv.write_setpoint(parameter, value)
    assert fake.writes == []
    drv.close()


def test_write_request_allowlist():
    assert SETPOINT_ADDRESSES == {"1.13.0", "1.13.2", "1.13.13"}
    assert build_write_request("1.13.0", "6.30") == b"J1.13.0s6.30\r"
    # dose counter, actuator on, calibration, loop on, temperature PID, temperature/stirrer control type
    for addr in ("2.13.29", "2.24.29", "1.30.0", "1.14.0", "3.0.2", "1.19.2", "1.19.13", "3.72.0"):
        with pytest.raises(DriverError):
            build_write_request(addr, "0")
    with pytest.raises(DriverError):
        build_write_request("1.13.0", "6.3\rJ2.13.29s0")      # no injection of a second command
    with pytest.raises(DriverError):
        build_write_request("1.19.0", "2")                     # External control: never
    assert build_write_request("1.19.0", "1") == b"J1.19.0s1\r"


def test_setpoint_command_end_to_end(fake):
    """services/commands.py with the real driver: validate, write, read back, confirm."""
    from fm2.config import settings
    from fm2.services.commands import write_setpoint as command
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    res = command(drv, "ph", 6.5, settings.setpoint_ranges)
    assert res.status == "confirmed" and res.old_value == 6.2 and res.readback_value == 6.5
    assert fake.writes == [("1.13.0", "6.50")]
    drv.close()


def test_missing_values_are_none():
    f = FakeController({"1.15.0": "6.5"})
    try:
        drv = make_driver("ez2control", "R1", f"127.0.0.1:{f.port}")
        drv.timeout_s = 0.5
        snap = drv.read()
        assert snap.ph == 6.5 and snap.temperature is None and snap.base_pump_pct is None
        drv.close()
    finally:
        f.stop()


def test_unreachable_controller_raises():
    drv = make_driver("ez2control", "R1", "127.0.0.1:9")  # nothing listens on port 9
    drv.timeout_s = 1
    with pytest.raises(DriverError):
        drv.read()


def test_address_required():
    with pytest.raises(ValueError):
        make_driver("ez2control", "R1", "")


def test_reconnects_when_controller_drops_idle_connection():
    f = FakeController(R2_VALUES, drop_after_answer=True)
    try:
        drv = make_driver("ez2control", "R2", f"127.0.0.1:{f.port}")
        import time
        for _ in range(3):
            assert drv.read().ph == 6.90   # every read succeeds despite the drop
            time.sleep(0.2)
        assert f.connections >= 3
        drv.close()
    finally:
        f.stop()


def test_write_refused_when_someone_else_is_logged_in(fake):
    fake.other_user_logged_in = True           # e.g. Engineer logged in on the display
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(DriverError, match="login refused"):
        drv.write_setpoint("ph", 6.3)
    assert fake.writes == [] and fake.ignored_writes == []   # setpoint never even sent
    assert fake.values["1.13.0"] == "6.20"
    drv.close()


def test_write_refused_with_wrong_password(fake, password_file):
    password_file.write_text("1234\n")
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(DriverError, match="login refused"):
        drv.write_setpoint("ph", 6.3)
    assert fake.writes == [] and fake.ignored_writes == []
    drv.close()


def test_no_password_no_write(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("FM2_EZ2_PASSWORD_FILE", str(tmp_path / "missing.txt"))
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    with pytest.raises(DriverError, match="no controller password"):
        drv.write_setpoint("ph", 6.3)
    assert fake.logins == [] and fake.writes == []
    drv.close()


def test_login_request_format():
    from fm2.drivers.ez2control import LOGOUT_REQUEST, build_login_request
    assert build_login_request(2, 0, "fake-test-pw") == b"J0.11s2|0|" + base64.b64encode(b"fake-test-pw") + b"\r"
    assert LOGOUT_REQUEST == b"J0.11s0|0|\r"
    for bad in (0, 3):                          # never View (pointless) nor Service
        with pytest.raises(DriverError):
            build_login_request(bad, 0, "x")
