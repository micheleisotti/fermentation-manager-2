"""2.2.2/2.3: buffer concentration and available volume, mmol, bottle-low alert, sampling volume, timeline."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from fm2.api.app import create_app
from fm2.config import Settings
from fm2.drivers.simulator import FakeClock, SimulatedController


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    drivers = {"R1": SimulatedController("R1", clock=clock, seed=1)}
    app = create_app(Settings(data_dir=tmp_path), drivers=drivers, clock=clock, start_acquisition=False,
                     db_path=tmp_path / "t.db")
    return TestClient(app), clock, app


def run_hours(app, clock, hours, step_min=1):
    for _ in range(int(hours * 60 / step_min)):
        clock.advance(minutes=step_min)
        app.state.acq.poll_once()


def test_buffer_frozen_mmol_and_bottle_alert(env):
    c, clock, app = env
    b = c.post("/api/buffers", json={"name": "NaOH 2 M", "molarity": 2.0}).json()
    assert b["molarity"] == 2.0
    e = c.post("/api/experiments", json={"name": "B2", "reactor_id": "R1", "buffer_id": b["id"],
                                        "buffer_volume_ml": 18}).json()
    assert e["buffer_molarity"] == 2.0 and e["buffer_equivalents"] == 1.0 and e["buffer_volume_ml"] == 18
    # editing the buffer in the Library does not change the planned experiment
    c.put(f"/api/buffers/{b['id']}", json={"molarity": 4.0})
    assert c.get(f"/api/experiments/{e['id']}").json()["buffer_molarity"] == 2.0
    c.post(f"/api/experiments/{e['id']}/start")
    run_hours(app, clock, 24, step_min=5)
    an = c.get(f"/api/experiments/{e['id']}/analysis").json()
    bd = an["base_dosing"]
    assert bd["total_ml"] and abs(bd["total_mmol"] - bd["total_ml"] * 2.0) < 1e-6
    # bottle alert once, when less than 10 % of 20 mL is left
    evs = c.get(f"/api/experiments/{e['id']}/events").json()
    low = [x for x in evs if x["kind"] == "buffer_low"]
    assert bd["total_ml"] > 16.2 and len(low) == 1 and "below 10 %" in low[0]["message"]
    # export carries the mmol column and the buffer metadata
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/experiments/{e['id']}/export.zip").content))
    names = z.namelist()
    ctrl = z.read(next(n for n in names if "controller" in n.lower())).decode()
    meta = z.read(next(n for n in names if "metadata" in n.lower())).decode()
    assert "base_cumulative_mmol" in ctrl and "buffer concentration (mol/L)" in meta


def test_sampling_volume_and_timeline(env):
    c, clock, app = env
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{e['id']}/start")
    run_hours(app, clock, 1)
    od = next(v for v in c.get("/api/variables").json() if v["name"] == "OD600")
    m = c.post(f"/api/experiments/{e['id']}/measurements",
               json={"variable_id": od["id"], "value": 0.42, "volume_taken_ml": 5}).json()
    assert m["volume_taken_ml"] == 5
    c.post("/api/reactors/R1/setpoints", json={"parameter": "stirrer", "value": 250})
    tl = c.get(f"/api/experiments/{e['id']}/timeline").json()
    cats = {x["category"] for x in tl}
    assert {"sampling", "change"} <= cats
    s = next(x for x in tl if x["category"] == "sampling")
    assert "OD600 = 0.42" in s["title"] and "5 mL taken" in s["detail"]
    assert tl == sorted(tl, key=lambda x: x["at"], reverse=True)
