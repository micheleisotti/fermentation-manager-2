"""2.3: manual pumps (hold to run) with the FM2-side safety, loop assignments, outputs lists."""
import json

import pytest
from fastapi.testclient import TestClient

from fm2.api.app import create_app
from fm2.config import Settings
from fm2.drivers.simulator import FakeClock, SimulatedController
from fm2.manual import ManualPumps, ManualRefused, HOLD_TIMEOUT_S


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path):
    sim = SimulatedController("R1")
    stops = []
    clk = Clock()
    m = ManualPumps({"R1": sim}, tmp_path / "manual.json", on_stop=lambda run, why, ml: stops.append((run.pump_id, why)), clock=clk)
    return sim, m, clk, stops


def test_hold_keepalive_and_signal_lost(tmp_path):
    sim, m, clk, stops = make(tmp_path)
    m.hold("R1", 29, "MI")
    assert sim.prime_on[29] is True
    assert json.loads((tmp_path / "manual.json").read_text()) == [["R1", 29]]
    clk.t += 1.0
    m.hold("R1", 29, "MI", start=False)          # keep-alive
    m.check()
    assert sim.prime_on[29] is True
    clk.t += HOLD_TIMEOUT_S + 0.1   # no signal
    m.check()
    assert sim.prime_on[29] is False and stops == [(29, "signal lost")]
    assert json.loads((tmp_path / "manual.json").read_text()) == []


def test_max_run_and_release(tmp_path):
    sim, m, clk, stops = make(tmp_path)
    m.hold("R1", 30, "MI")
    for _ in range(70):
        clk.t += 1.0
        try:
            m.hold("R1", 30, "MI", start=False)
        except ManualRefused as ex:
            assert "60 s limit" in str(ex)
        m.check()
    assert sim.prime_on[30] is False and stops == [(30, "60 s limit")]
    m.hold("R1", 30, "MI")
    m.release("R1", 30)
    assert stops[-1] == (30, "released")


def test_refused_while_loop_on_and_recover(tmp_path):
    sim, m, clk, stops = make(tmp_path)
    sim.write_loop_active("ph", True)
    try:
        m.hold("R1", 29, "MI")
        assert False, "should refuse"
    except ManualRefused as ex:
        assert "loop is on" in str(ex)
    sim.write_loop_active("ph", False)
    sim.prime_on[28] = True
    (tmp_path / "manual.json").write_text(json.dumps([["R1", 28]]))
    assert m.recover() == [("R1", 28)] and sim.prime_on[28] is False


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    app = create_app(Settings(data_dir=tmp_path), drivers={"R1": SimulatedController("R1", clock=clock, seed=1)}, clock=clock,
                     start_acquisition=False, db_path=tmp_path / "t.db")
    return TestClient(app), clock, app


def test_api_pumps_assignment_and_outputs(env):
    c, clock, app = env
    pumps = c.get("/api/reactors/R1/pumps").json()
    assert {p["name"] for p in pumps["pumps"]} >= {"Alkali", "Addition 2", "Antifoam"}
    assert c.post("/api/reactors/R1/pumps/29/hold").json()["running"] is True
    assert c.post("/api/reactors/R1/pumps/29/release").json()["was_running"] is True
    cfg = c.get("/api/reactors/R1/loop-config?fresh=true").json()
    assert any(o["gas"] for o in cfg["free_outputs"]) and any(x["kind"] == "input" for x in cfg["not_installed"])
    r = c.post("/api/reactors/R1/loop-assignment", json={"loop": "ph", "actuator_id": 28, "side": 0}).json()
    assert r["confirmed"] is True
    ph = next(x for x in r["loops"] if x["key"] == "ph")
    assert [a["name"] for a in ph["actuators"]] == ["Alkali"]
    assert any(o["name"] == "Addition 2" for o in r["free_outputs"])
    r = c.post("/api/reactors/R1/loop-assignment", json={"loop": "ph", "actuator_id": 28, "side": -1}).json()
    assert r["confirmed"] is True


def test_do2_and_stirrer_values(env):
    c, clock, app = env
    r = c.post("/api/reactors/R1/setpoints", json={"parameter": "do2", "value": 30}).json()
    assert r["status"] == "confirmed" and r["readback_value"] == 30
    r = c.post("/api/reactors/R1/limits", json={"parameter": "stirrer", "low": 50, "high": 400}).json()
    assert [x["status"] for x in r] == ["confirmed", "confirmed"]
