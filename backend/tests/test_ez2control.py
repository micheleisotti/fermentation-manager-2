"""ez2-Control driver against a fake controller speaking the same WebSocket protocol."""

import threading

import pytest
from websockets.sync.server import serve

from fm2.drivers import DriverError, make_driver
from fm2.drivers.ez2control import READ_MAP, build_read_request, parse_frame, to_float

# Values read from R2-dx on 2026-11-09 (no culture, probes disconnected), plus a pump run.
R2_VALUES = {
    "1.15.0": "6.90", "1.13.0": "6.20", "1.4.0": "6.50", "1.5.0": "6.00", "1.19.0": "0",
    "3.0.0": "100.00", "3.3.0": "2.00", "3.6.0": "10.00",
    "1.15.2": "22.7", "1.13.2": "37.0", "1.4.2": "38.5", "1.5.2": "5.0",
    "1.15.13": "0", "1.13.13": "400",
    "2.1.29": "Alkali", "2.13.29": "1.61", "2.12.29": "4.80", "2.4.29": "4.80",
    "1.15.3": "NO CONT", "1.4.3": "1.0E+05",
}


class FakeController:
    """Answers J<addr>S requests; records every request; splits answers over two frames.
    With drop_after_answer=True it kills the TCP connection after each answer without a
    close frame, like the real controller does with idle connections."""

    def __init__(self, values, drop_after_answer=False):
        self.values = dict(values)
        self.drop_after_answer = drop_after_answer
        self.connections = 0
        self.requests: list[str] = []
        self.server = serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self, ws):
        self.connections += 1
        for msg in ws:
            text = msg.decode("ascii") if isinstance(msg, bytes) else msg
            lines = [l for l in text.split("\r") if l]
            self.requests.extend(lines)
            answers = []
            for l in lines:
                assert l.startswith("J") and l.endswith("S"), f"non-read request sent: {l!r}"
                addr = l[1:-1]
                if addr in self.values:
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


def test_never_writes(fake):
    drv = make_driver("ez2control", "R2", f"127.0.0.1:{fake.port}")
    drv.read()
    with pytest.raises(DriverError):
        drv.write_setpoint("ph", 6.5)
    with pytest.raises(DriverError):
        drv.write_ph_control(None)
    assert fake.requests and all(r.endswith("S") for r in fake.requests)
    assert set(r[1:-1] for r in fake.requests) == set(READ_MAP.values())
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
