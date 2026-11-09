"""End-to-end: two simulated reactors, a parallel run, recording, alerts, analysis, export."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fm2.api.app import create_app
from fm2.config import Settings
from fm2.drivers.simulator import FakeClock, SimulatedController


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    drivers = {"R1": SimulatedController("R1", clock=clock, seed=1),
               "R2": SimulatedController("R2", clock=clock, seed=2, lag_h=5.5, mu_max=0.7)}
    app = create_app(Settings(data_dir=tmp_path), drivers=drivers, clock=clock, start_acquisition=False,
                     db_path=tmp_path / "t.db")
    client = TestClient(app)
    return client, clock, app, drivers


def run_hours(app, clock, hours, step_min=1):
    for _ in range(int(hours * 60 / step_min)):
        clock.advance(minutes=step_min)
        app.state.acq.poll_once()


def test_full_workflow(env):
    c, clock, app, drivers = env
    med = c.post("/api/media", json={"name": "MRS"}).json()
    prof = c.post("/api/profiles", json={"name": "plantarum standard 1", "ph_setpoint": 6.0,
                                         "temperature_setpoint": 30.0, "stirrer_setpoint": 200.0,
                                         "ph_control_mode": "pid", "ph_pid": {"P": 2.0, "I": 90.0, "D": 0.0}}).json()
    a = c.post("/api/experiments", json={"name": "B2 riboflavin", "reactor_id": "R1", "strain": "Lp187_B2",
                                        "medium_id": med["id"], "profile_id": prof["id"]}).json()
    b = c.post("/api/experiments", json={"name": "WT control", "reactor_id": "R2", "strain": "Lp187",
                                        "profile_id": prof["id"]}).json()
    assert a["code"] == "FM-001" and a["status"] == "planned" and a["planned_date"]

    linked = c.post(f"/api/experiments/{a['id']}/parallel", json={"partner_id": b["id"]}).json()
    assert linked["parallel_run_code"] == "PR-001" and linked["parallel_partner_codes"] == ["FM-002"]

    check = c.get(f"/api/experiments/{a['id']}/plan-check").json()
    pid_row = next(r for r in check["rows"] if r["parameter"] == "ph_pid")
    assert not pid_row["match"]
    loaded = c.post(f"/api/experiments/{a['id']}/load-profile").json()
    assert all(r["status"] == "confirmed" for r in loaded["results"])
    assert drivers["R1"].ph_control.pid == {"P": 2.0, "I": 90.0, "D": 0.0}

    for e in (a, b):
        assert c.post(f"/api/experiments/{e['id']}/start").json()["status"] == "fermenting"
    assert c.post(f"/api/experiments/{a['id']}/start").status_code == 409

    ev = c.get(f"/api/experiments/{a['id']}/events").json()
    assert any(x["kind"] == "out_of_limits_at_start" for x in ev)  # starts at pH 6.55 > 6.3

    od = c.get("/api/variables").json()
    od_id = next(v["id"] for v in od if v["name"] == "OD600")
    for h in range(0, 17, 2):
        if h:
            run_hours(app, clock, 2)
        for e, drv in ((a, drivers["R1"]), (b, drivers["R2"])):
            c.post(f"/api/experiments/{e['id']}/measurements",
                   json={"variable_id": od_id, "value": round(drv._x, 3), "author": "michele"})

    ev = c.get(f"/api/experiments/{a['id']}/events").json()
    assert any(x["kind"] == "setpoint_reached" for x in ev)

    live = c.get("/api/reactors/live").json()
    assert live[0]["online"] and live[0]["experiment"]["code"] == "FM-001"
    assert live[0]["experiment"]["t_h"] == pytest.approx(16, abs=0.05)

    # remote setpoint change: confirmed, logged, not flagged as external
    r = c.post("/api/reactors/R1/setpoints", json={"parameter": "temperature", "value": 25.0, "origin": "remote"}).json()
    assert r["status"] == "confirmed" and r["warnings"]
    run_hours(app, clock, 0.2)
    ev = c.get(f"/api/experiments/{a['id']}/events").json()
    assert not any(x["kind"] == "external_setpoint_change" for x in ev)
    assert c.post("/api/reactors/R1/setpoints", json={"parameter": "temperature", "value": 90}).status_code == 422
    assert len(c.get(f"/api/experiments/{a['id']}/setpoint-log").json()) >= 4

    readings = c.get(f"/api/experiments/{a['id']}/readings").json()
    assert readings["rows"][0][0] == pytest.approx(0.0)
    cum = [row[readings["columns"].index("base_cumulative_ml")] for row in readings["rows"]]
    assert cum == sorted(cum) and cum[-1] > 5  # staircase, monotonic

    for e in (a, b):
        assert c.post(f"/api/experiments/{e['id']}/end").json()["status"] == "completed"

    an = c.get(f"/api/experiments/{a['id']}/analysis").json()
    assert an["fit"]["mu_max_per_h"] > 0 and an["base_dosing"]["total_ml"] > 5 and an["rate_bins"]

    sess = c.post("/api/analysis-sessions", json={"name": "B2 vs WT", "members": {a["id"]: "A", b["id"]: "B"}}).json()
    res = c.get(f"/api/analysis-sessions/{sess['id']}/results").json()
    assert res["parallel_run_flags"][0]["parallel_run"] == "PR-001"
    assert res["parallel_run_flags"][0]["same_condition"] is False
    assert res["comparisons"]["mu_max_per_h"]["p_value"] is None  # n = 1 per condition: no test

    x = c.get(f"/api/experiments/{a['id']}/export.xlsx?resample_min=5")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_manual_edit_is_audited_and_time_from_start(env):
    c, clock, app, _ = env
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    od_id = c.get("/api/variables").json()[0]["id"]
    assert c.post(f"/api/experiments/{e['id']}/measurements", json={"variable_id": od_id, "value": 1}).status_code == 409
    c.post(f"/api/experiments/{e['id']}/start")
    clock.advance(hours=3)
    m = c.post(f"/api/experiments/{e['id']}/measurements", json={"variable_id": od_id, "value": 0.5}).json()
    assert m["t_h"] == pytest.approx(3.0)
    earlier = (clock() - timedelta(minutes=30)).isoformat()
    m2 = c.patch(f"/api/measurements/{m['id']}", json={"sampled_at": earlier, "author": "michele"}).json()
    assert m2["t_h"] == pytest.approx(2.5)


def test_silent_controller_alert(env):
    c, clock, app, drivers = env
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{e['id']}/start")
    drivers["R1"].fail_reads = True
    run_hours(app, clock, 0.2)
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    assert any(x["kind"] == "controller_silent" for x in ev)
    assert c.get("/api/reactors/live").json()[0]["online"] is False


def test_delete_profile(env):
    c, clock, app, drivers = env
    p1 = c.post("/api/profiles", json={"name": "to delete", "ph_setpoint": 6.0}).json()
    p2 = c.post("/api/profiles", json={"name": "in use", "ph_setpoint": 6.2}).json()
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1", "profile_id": p2["id"]}).json()
    assert c.delete(f"/api/profiles/{p1['id']}").status_code == 200
    r = c.delete(f"/api/profiles/{p2['id']}")                 # planned experiment uses it
    assert r.status_code == 409 and e["code"] in r.json()["detail"]
    assert c.post(f"/api/experiments/{e['id']}/start").status_code == 200
    r = c.delete(f"/api/profiles/{p2['id']}")                 # started: keeps its plan snapshot
    assert r.status_code == 200 and r.json()["unlinked_experiments"] == [e["code"]]
    assert [p["name"] for p in c.get("/api/profiles").json()] == []
    assert c.get(f"/api/experiments/{e['id']}").status_code == 200
    assert c.delete("/api/profiles/9999").status_code == 404


def test_delete_experiment(env):
    c, clock, app, drivers = env
    a = c.post("/api/experiments", json={"name": "a", "reactor_id": "R1"}).json()
    b = c.post("/api/experiments", json={"name": "b", "reactor_id": "R2"}).json()
    assert c.post(f"/api/experiments/{a['id']}/parallel", json={"partner_id": b["id"]}).status_code == 200
    for x in (a, b):
        assert c.post(f"/api/experiments/{x['id']}/start").status_code == 200
    run_hours(app, clock, 2, step_min=10)
    var = c.get("/api/variables").json()[0]
    assert c.post(f"/api/experiments/{a['id']}/measurements", json={"variable_id": var["id"], "value": 0.5}).status_code in (200, 201)
    assert c.delete(f"/api/experiments/{a['id']}?confirm={a['code']}").status_code == 409   # still recording
    c.post(f"/api/experiments/{a['id']}/end")
    assert c.delete(f"/api/experiments/{a['id']}?confirm=FM-999").status_code == 400        # wrong code
    r = c.delete(f"/api/experiments/{a['id']}?confirm={a['code']}")
    assert r.status_code == 200 and r.json()["deleted"] == a["code"]
    assert c.get(f"/api/experiments/{a['id']}").status_code == 404
    assert c.get(f"/api/experiments/{a['id']}/readings").status_code == 404
    partner = c.get(f"/api/experiments/{b['id']}").json()
    assert partner["parallel_run_code"] is None and partner["status"] == "fermenting"
    from sqlalchemy import func, select
    from fm2.models import ControllerReading, Event, ManualMeasurement
    with app.state.sessions() as s:
        for model in (ControllerReading, ManualMeasurement, Event):
            assert s.scalar(select(func.count()).select_from(model).where(model.experiment_id == a["id"])) == 0
        assert s.scalar(select(func.count()).select_from(ControllerReading).where(ControllerReading.experiment_id == b["id"])) > 0


def test_change_limits_and_profile_limits(env):
    c, clock, app, drivers = env
    r = c.post("/api/reactors/R1/limits", json={"parameter": "ph", "low": 5.7, "high": 6.4, "author": "M"})
    assert r.status_code == 200 and [x["status"] for x in r.json()] == ["confirmed", "confirmed"]
    app.state.acq.poll_once()
    snap = drivers["R1"].read()
    assert (snap.ph_low_limit, snap.ph_high_limit) == (5.7, 6.4)
    assert c.post("/api/reactors/R1/limits", json={"parameter": "ph", "low": 6.5, "high": 6.0}).status_code == 422
    assert c.post("/api/reactors/R1/limits", json={"parameter": "stirrer", "low": 1, "high": 2}).status_code == 422
    # setpoint 6.0 outside new limits -> warning
    r = c.post("/api/reactors/R1/limits", json={"parameter": "ph", "low": 6.1})
    assert r.json()[0]["warnings"] and "outside the new limits" in r.json()[0]["warnings"][0]

    p = c.post("/api/profiles", json={"name": "lim", "ph_setpoint": 6.0, "ph_low_limit": 5.9, "ph_high_limit": 6.2,
                                      "temperature_high_limit": 33.0}).json()
    assert p["ph_low_limit"] == 5.9 and p["temperature_low_limit"] is None
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R2", "profile_id": p["id"]}).json()
    rows = {x["parameter"]: x for x in c.get(f"/api/experiments/{e['id']}/plan-check").json()["rows"]}
    assert rows["ph_limits"]["match"] is False and not rows["ph_limits"].get("read_only")
    res = c.post(f"/api/experiments/{e['id']}/load-profile").json()["results"]
    assert {x["parameter"]: x["status"] for x in res}["ph_low_limit"] == "confirmed"
    rows = {x["parameter"]: x for x in c.get(f"/api/experiments/{e['id']}/plan-check").json()["rows"]}
    assert rows["ph_limits"]["match"] and rows["temperature_limits"]["match"]
    assert drivers["R2"].read().temperature_high_limit == 33.0


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    from fm2.api.app import create_app
    from fm2.config import Settings
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)        # a profiles table as it was before process limits
    con.execute("CREATE TABLE controller_profiles (id INTEGER PRIMARY KEY, name VARCHAR(200) UNIQUE, "
                "ph_setpoint FLOAT, temperature_setpoint FLOAT, stirrer_setpoint FLOAT, ph_control_mode VARCHAR(8), "
                "ph_pid JSON, notes TEXT, author VARCHAR(100), created_at DATETIME)")
    con.execute("INSERT INTO controller_profiles (name, ph_setpoint, notes, author) VALUES ('old', 6.1, '', '')")
    con.commit(); con.close()
    app = create_app(Settings(data_dir=tmp_path), start_acquisition=False, db_path=db,
                     drivers={"R1": SimulatedController("R1"), "R2": SimulatedController("R2")})
    c = TestClient(app)
    profs = c.get("/api/profiles").json()
    assert profs[0]["name"] == "old" and profs[0]["ph_setpoint"] == 6.1 and profs[0]["ph_low_limit"] is None
