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
    assert res["comparisons_by_key"]["mu_max_per_h"]["p_value"] is None  # n = 1 per condition: no test
    assert res["conditions"] == ["A", "B"] and res["curves"]["A"]["fit"] is not None
    assert {o["key"] for o in res["variable_options"]} >= {"OD600", "ph", "base_rate"}
    r2 = c.get(f"/api/analysis-sessions/{sess['id']}/results?variable=base_rate&step_h=1").json()
    assert r2["curves"]["A"]["n"] == 1 and len(r2["curves"]["A"]["t"]) > 3
    upd = c.put(f"/api/analysis-sessions/{sess['id']}", json={"name": "renamed", "members": {a["id"]: "WT"},
                                                              "conditions": ["B2", "WT"], "model": "logistic"}).json()
    assert upd["conditions"] == ["B2", "WT"] and upd["n_by_condition"] == {"B2": 0, "WT": 1}
    assert c.get("/api/analysis-sessions").json()[0]["name"] == "renamed"
    f = c.get(f"/api/analysis-sessions/{sess['id']}/figure.svg?variable=OD600&dpi=100")
    assert f.status_code == 200 and f.content[:5] == b"<?xml"
    x = c.get(f"/api/analysis-sessions/{sess['id']}/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.put(f"/api/analysis-sessions/{sess['id']}", json={"name": "x", "members": {}, "conditions": ["A", "A"]}).status_code == 422
    assert c.delete(f"/api/analysis-sessions/{sess['id']}").json()["deleted"] == sess["code"]

    x = c.get(f"/api/experiments/{a['id']}/export.xlsx?resample_min=5")
    assert x.status_code == 200 and x.content[:2] == b"PK"

    for model in ("gompertz", "logistic", "baranyi"):
        an = c.get(f"/api/experiments/{a['id']}/analysis?model={model}&step_h=2").json()
        assert an["fits"]["OD600"]["params"]["model"] == model, an["fits"]["OD600"]
        assert an["fits"]["OD600"]["params"]["mu_max_per_h"] > 0 and len(an["fits"]["OD600"]["curve"]["t"]) == 200
    assert an["summary"]["final_od"] > 0 and an["summary"]["total_base_ml"] > 5
    assert an["experiment"]["partner"]["code"] == b["code"]
    s_ = an["series"]
    assert len(s_["t"]) == len(s_["ph"]) == len(s_["base_rate"]) and s_["t"][0] == pytest.approx(1.0)
    assert {d["key"] for d in an["series_defs"]} >= {"ph", "base_rate", "temperature", "stirrer"}
    assert "do2" not in s_  # channel not enabled
    assert c.get(f"/api/experiments/{a['id']}/analysis?model=nope").status_code == 422

    for fmt, magic in (("png", b"\x89PNG"), ("svg", b"<?xml"), ("pdf", b"%PDF")):
        f = c.get(f"/api/experiments/{a['id']}/figure.{fmt}?vars=OD600,ph,base_rate&dpi=100&theme=bw")
        assert f.status_code == 200 and f.content[:len(magic)] == magic, fmt
    assert c.get(f"/api/experiments/{a['id']}/figure.png?vars=bogus").status_code == 422

    import io, zipfile
    z = c.get(f"/api/experiments/{a['id']}/export.zip?blocks=controller,metadata&resample_min=15")
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert z.status_code == 200 and any("controller_data" in n for n in names) and any("change_log" in n for n in names)
    assert not any("manual" in n for n in names)
    assert c.get(f"/api/experiments/{a['id']}/export.zip?blocks=nope").status_code == 422


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


def test_frontend_cache_headers(tmp_path):
    from fm2.api.app import create_app
    from fm2.config import Settings
    dist = tmp_path / "dist"; (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    app = create_app(Settings(data_dir=tmp_path, frontend_dist=dist), start_acquisition=False, db_path=tmp_path / "c.db",
                     drivers={"R1": SimulatedController("R1"), "R2": SimulatedController("R2")})
    c = TestClient(app)
    for path in ("/", "/experiments/3"):
        r = c.get(path)
        assert r.status_code == 200 and "app" in r.text and r.headers["cache-control"] == "no-cache"
    r = c.get("/assets/index-abc123.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]


def test_channels(env):
    c, clock, app, drivers = env
    a = c.post("/api/experiments", json={"name": "default", "reactor_id": "R1"}).json()
    assert a["channels"] == ["temperature", "ph", "stirrer"]
    assert c.post("/api/experiments", json={"name": "bad", "reactor_id": "R2", "channels": ["ph", "foam"]}).status_code == 422
    p = c.post("/api/profiles", json={"name": "p", "ph_setpoint": 6.1, "temperature_setpoint": 33.0}).json()
    b = c.post("/api/experiments", json={"name": "ph only", "reactor_id": "R2", "profile_id": p["id"],
                                        "channels": ["do2", "ph"]}).json()
    assert b["channels"] == ["ph", "do2"]                         # canonical order
    rows = [r["parameter"] for r in c.get(f"/api/experiments/{b['id']}/plan-check").json()["rows"]]
    assert "temperature_setpoint" not in rows and "ph_setpoint" in rows
    res = c.post(f"/api/experiments/{b['id']}/load-profile").json()["results"]
    assert [r["parameter"] for r in res] == ["ph"]                 # T setpoint not written: channel off
    assert drivers["R2"].read().temperature_setpoint == 30.0
    c.post(f"/api/experiments/{b['id']}/start")
    run_hours(app, clock, 0.5, step_min=5)
    r = c.get(f"/api/experiments/{b['id']}/readings").json()
    assert {"do2", "heating_pct", "n2_pct"} <= set(r["columns"])
    i = r["columns"].index("do2")
    assert all(row[i] is not None for row in r["rows"])            # all channels recorded anyway
    assert c.patch(f"/api/experiments/{b['id']}", json={"channels": ["ph", "temperature"]}).json()["channels"] == ["temperature", "ph"]


def test_users_pin_and_signatures(env):
    c, clock, app, _ = env
    users = c.get("/api/users").json()
    assert [u["name"] for u in users] == ["Guest"]
    assert c.post("/api/users", json={"name": "Michele", "pin": "12"}).status_code == 422
    m = c.post("/api/users", json={"name": "Michele", "pin": "2468"}).json()
    assert m["user"]["has_pin"] and m["token"]
    assert c.post("/api/users", json={"name": "michele"}).status_code == 409
    carl = c.post("/api/users", json={"name": "Carlettino"}).json()
    mid = m["user"]["id"]

    assert c.post("/api/auth/login", json={"user_id": mid, "pin": "0000"}).status_code == 401
    tok = c.post("/api/auth/login", json={"user_id": mid, "pin": "2468"}).json()["token"]
    h = {"X-FM2-Token": tok}
    assert c.get("/api/auth/me", headers=h).json()["user"]["name"] == "Michele"
    assert c.get("/api/auth/me", headers={"X-FM2-Token": tok[:-2] + "xx"}).json()["user"] is None
    assert c.post("/api/auth/login", json={"user_id": carl["user"]["id"]}).status_code == 200  # no PIN

    # the server, not the browser, decides the signature
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1", "author": "Someone else"}, headers=h).json()
    c.post(f"/api/experiments/{e['id']}/start", json={"checklist": ["Probes calibrated"]}, headers=h)
    od = c.get("/api/variables").json()[0]["id"]
    mm = c.post(f"/api/experiments/{e['id']}/measurements", json={"variable_id": od, "value": 0.1, "author": "Michele"}).json()
    assert mm["author"] == "Guest"   # no token → Guest, whatever the body says
    c.post("/api/reactors/R1/setpoints", json={"parameter": "temperature", "value": 31}, headers=h)
    assert any(x["author"] == "Michele" for x in c.get(f"/api/experiments/{e['id']}/setpoint-log").json())
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    assert any("started by Michele" in x["message"] and "Probes calibrated" in x["message"] for x in ev)

    # PIN-protected user can be changed only when signed in as them; PIN change invalidates old tokens
    assert c.put(f"/api/users/{mid}", json={"archived": True}).status_code == 403
    r = c.put(f"/api/users/{mid}", json={"pin": "13579"}, headers=h).json()
    assert c.get("/api/auth/me", headers=h).json()["user"] is None
    assert c.get("/api/auth/me", headers={"X-FM2-Token": r["token"]}).json()["user"]["name"] == "Michele"
    assert c.put(f"/api/users/{users[0]['id']}", json={"pin": "1234"}).status_code == 422  # Guest

    for _ in range(5):
        c.post("/api/auth/login", json={"user_id": mid, "pin": "0000"})
    assert c.post("/api/auth/login", json={"user_id": mid, "pin": "13579"}).status_code == 429


def test_library_archive_and_end_outcome(env):
    c, clock, app, _ = env
    # variables: roles, rename, archive, delete only if unused
    v = c.post("/api/variables", json={"name": "Residual sugar", "unit": "g/L", "category": "substrate"}).json()
    assert c.post("/api/variables", json={"name": "residual sugar", "unit": "", "category": "other"}).status_code == 409
    assert c.post("/api/variables", json={"name": "X", "unit": "", "category": "nonsense"}).status_code == 422
    assert c.put(f"/api/variables/{v['id']}", json={"unit": "mM"}).json()["unit"] == "mM"
    # media: rename, archive, delete refused when used
    m = c.post("/api/media", json={"name": "MRS"}).json()
    e = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1", "medium_id": m["id"]}).json()
    assert c.get("/api/media").json()[0]["n_used"] == 1
    assert c.delete(f"/api/media/{m['id']}").status_code == 409
    assert c.put(f"/api/media/{m['id']}", json={"name": "MRS broth", "archived": True}).json()["archived"] is True
    assert c.get("/api/media").json() == [] and len(c.get("/api/media?include_archived=true").json()) == 1
    # substrate variables are plotted, not fitted
    c.post(f"/api/experiments/{e['id']}/start")
    for i in range(5):
        clock.advance(hours=1)
        c.post(f"/api/experiments/{e['id']}/measurements", json={"variable_id": v["id"], "value": 20 - 3 * i})
    assert c.delete(f"/api/variables/{v['id']}").status_code == 409
    an = c.get(f"/api/experiments/{e['id']}/analysis").json()
    assert an["fits"]["Residual sugar"]["skipped"] is True and len(an["manual"]["Residual sugar"]["t"]) == 5
    # one End of experiment with outcome + note; archive
    assert c.post(f"/api/experiments/{e['id']}/end", json={"outcome": "maybe"}).status_code == 422
    out = c.post(f"/api/experiments/{e['id']}/end", json={"outcome": "aborted", "note": "contaminated"}).json()
    assert out["status"] == "aborted" and out["end_note"] == "contaminated"
    assert any("aborted" in x["message"] and "contaminated" in x["message"] for x in c.get(f"/api/experiments/{e['id']}/events").json())
    assert c.patch(f"/api/experiments/{e['id']}", json={"archived": True}).json()["archived"] is True
    assert c.get(f"/api/experiments/{e['id']}").json()["n_sessions"] == 0


def test_archive_copy_on_end_and_on_demand(env, tmp_path):
    c, clock, app, _ = env
    arch = tmp_path / "usb"
    e = c.post("/api/experiments", json={"name": "Riboflavin: pH 6/7", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{e['id']}/start")
    clock.advance(hours=1)
    # no folder yet: the run ends anyway, the copy reports why it was not saved
    out = c.post(f"/api/experiments/{e['id']}/end", json={"outcome": "completed", "save_copy": True}).json()
    assert out["status"] == "completed" and "No archive folder" in out["copy"]["error"]
    bk = c.get("/api/settings").json()["backup"]
    bk["archive_destination"] = str(arch)
    assert c.put("/api/settings/backup", json=bk).status_code == 200
    r = c.post(f"/api/experiments/{e['id']}/archive-copy", json={})
    assert r.status_code == 502 and "not found" in r.json()["detail"]   # USB stick "unplugged"
    arch.mkdir()
    r = c.post(f"/api/experiments/{e['id']}/archive-copy", json={"format": "zip"}).json()
    name = r["path"].split("/")[-1].split("\\")[-1]
    import re as _re
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}_FM-\d{3}_Riboflavin- pH 6-7\.zip", name), name
    assert (arch / name).stat().st_size == r["bytes"] > 0
    d = c.get(f"/api/experiments/{e['id']}/export.xlsx")
    assert "filename*=UTF-8''" in d.headers["content-disposition"] and "_FM-" in d.headers["content-disposition"]


def test_prepare_phase_and_plan_params(env):
    c, clock, app, _ = env
    prof = c.post("/api/profiles", json={"name": "plantarum", "ph_setpoint": 6.0, "temperature_setpoint": 30,
                                         "ph_low_limit": 5.7, "ph_high_limit": 6.3}).json()
    e = c.post("/api/experiments", json={"name": "prep", "reactor_id": "R1", "profile_id": prof["id"]}).json()
    assert e["plan_params"]["ph_setpoint"] == 6.0 and e["profile_modified"] is False
    # edit one value: the plan becomes "custom, based on plantarum"
    pp = dict(e["plan_params"], temperature_setpoint=32)
    e = c.patch(f"/api/experiments/{e['id']}", json={"plan_params": pp}).json()
    assert e["profile_modified"] is True and e["plan_params"]["temperature_setpoint"] == 32
    bad = dict(pp, ph_low_limit=6.5)
    assert c.patch(f"/api/experiments/{e['id']}", json={"plan_params": bad}).status_code == 422
    rows = {r["parameter"]: r for r in c.get(f"/api/experiments/{e['id']}/plan-check").json()["rows"]}
    assert rows["temperature_setpoint"]["plan"] == 32

    # Go to fermentation: monitoring and readings, no t = 0 yet
    e = c.post(f"/api/experiments/{e['id']}/prepare").json()
    assert e["status"] == "preparing" and e["started_at"] is None
    live = {r["reactor_id"]: r for r in c.get("/api/reactors/live").json()}
    assert live["R1"]["experiment"]["status"] == "preparing"
    other = c.post("/api/experiments", json={"name": "other", "reactor_id": "R1"}).json()
    assert c.post(f"/api/experiments/{other['id']}/prepare").status_code == 409   # reactor in use
    r = c.post(f"/api/experiments/{e['id']}/load-profile").json()
    assert any(x["parameter"] == "temperature" and x["status"] == "confirmed" for x in r["results"])
    for _ in range(3):
        clock.advance(hours=0.5)
        app.state.acq.poll_once()
    def tcol(url):
        j = c.get(url).json()
        i = j["columns"].index("t_h")
        return [row[i] for row in j["rows"]]
    assert tcol(f"/api/experiments/{e['id']}/readings?since_h=-10") == []   # nothing recorded before Start
    e = c.post(f"/api/experiments/{e['id']}/start").json()
    assert e["status"] == "fermenting"
    rd = tcol(f"/api/experiments/{e['id']}/readings?since_h=-10")
    assert rd and min(rd) >= 0
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    assert any(x["kind"] == "prepared" and x["t_h"] < -1.4 for x in ev)
    assert c.get(f"/api/experiments/{e['id']}").json()["plan_snapshot"]["custom"] is True


def test_counter_reset_is_stitched_in_readings_and_logged(env):
    c, clock, app, drivers = env
    e = c.post("/api/experiments", json={"name": "reset", "reactor_id": "R1", "strain": "Lp187"}).json()
    c.post(f"/api/experiments/{e['id']}/start")
    run_hours(app, clock, 8)
    before = drivers["R1"]._base_ml
    assert before > 0.5
    drivers["R1"]._base_ml = 0.0          # counter reset on the controller (power cycle)
    run_hours(app, clock, 2)
    r = c.get(f"/api/experiments/{e['id']}/readings").json()
    cols = r["columns"]
    cum = [row[cols.index("base_cumulative_ml")] for row in r["rows"]]
    assert "base_counter_raw_ml" in cols
    assert all(b >= a - 1e-9 for a, b in zip(cum, cum[1:]))   # never goes back
    assert cum[-1] >= before - 0.05
    late = c.get(f"/api/experiments/{e['id']}/readings?since_h=9").json()
    assert late["rows"][-1][late["columns"].index("base_cumulative_ml")] == pytest.approx(cum[-1])
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    assert sum(x["kind"] == "counter_reset" for x in ev) == 1
    p = c.get(f"/api/experiments/{e['id']}/analysis").json()
    assert p["summary"]["total_base_ml"] >= before - 0.05


def test_loop_config_temperature_profile_and_assignment_check(env):
    from dataclasses import replace
    c, clock, app, drivers = env
    cfg = c.get("/api/reactors/R1/loop-config").json()
    assert cfg["reachable"] and {lp["key"] for lp in cfg["loops"]} >= {"ph", "temperature", "do2", "level", "redox"}
    do2 = next(lp for lp in cfg["loops"] if lp["key"] == "do2")
    assert any("cascade" in w for w in do2["warnings"])
    rec = cfg["assignments"]
    prof = c.post("/api/profiles", json={"name": "T and pH", "ph_setpoint": 6.0, "ph_control_mode": "pid",
                                         "ph_pid": {"P": 3.0, "I": 50.0, "D": 0.0},
                                         "temperature_setpoint": 30.0, "temperature_control_mode": "pid",
                                         "temperature_pid": {"P": 25.2, "I": 3053.5, "D": 28.3},
                                         "loop_assignments": rec}).json()
    assert prof["temperature_control_mode"] == "pid" and prof["loop_assignments"]["ph"][0]["name"] == "Alkali"
    e = c.post("/api/experiments", json={"name": "loops", "reactor_id": "R1", "strain": "Lp187",
                                        "profile_id": prof["id"]}).json()
    chk = c.get(f"/api/experiments/{e['id']}/plan-check").json()
    assert any(r["parameter"] == "temperature_control_mode" and not r["match"] for r in chk["rows"])
    assert chk["assignment_differences"] == {}
    r = c.post(f"/api/experiments/{e['id']}/load-profile").json()["results"]
    assert {x["parameter"]: x["status"] for x in r}["temperature_control"] == "confirmed"
    assert drivers["R1"].temperature_control.mode == "pid" and drivers["R1"].temperature_control.pid["P"] == 25.2

    # someone removes Addition 2 from the pH loop on the display: pH loop parameters are no longer loaded
    lc = drivers["R1"].loop_config
    ph = next(lp for lp in lc.loops if lp.key == "ph")
    drivers["R1"].loop_config = replace(lc, loops=tuple(replace(ph, actuators=ph.actuators[:1]) if lp is ph else lp
                                                        for lp in lc.loops))
    clock.advance(minutes=1)    # past the loop-config cache
    from fm2.drivers.base import PhControl
    drivers["R1"].ph_control = PhControl("pid", {"P": 1.0, "I": 60.0, "D": 0.0})
    chk = c.get(f"/api/experiments/{e['id']}/plan-check").json()
    assert "Addition 2" in chk["assignment_differences"]["ph"]
    r = {x["parameter"]: x for x in c.post(f"/api/experiments/{e['id']}/load-profile").json()["results"]}
    assert r["ph_control"]["status"] == "skipped" and r["temperature_control"]["status"] == "confirmed"
    assert drivers["R1"].ph_control.pid == {"P": 1.0, "I": 60.0, "D": 0.0}   # untouched
    app.state.acq.poll_once()
    live = c.get("/api/reactors/live").json()[0]
    assert live["temperature_control"]["mode"] == "pid"


def test_start_stop_selected_loops_settings_and_external_switch(env):
    c, clock, app, drivers = env
    e = c.post("/api/experiments", json={"name": "loops on", "reactor_id": "R1", "strain": "Lp187",
                                        "channels": ["temperature", "ph", "stirrer", "do2"]}).json()
    c.post(f"/api/experiments/{e['id']}/prepare")
    st = c.get("/api/reactors/R1/loops").json()
    by = {lp["key"]: lp for lp in st["loops"]}
    assert st["allowed"] == ["ph", "temperature", "stirrer"]
    assert by["ph"]["allowed"] and by["ph"]["start_refused"] is None
    assert "Settings" in by["do2"]["start_refused"] and by["do2"]["also_switches"] == ["Stirrer"]
    assert "actuator" in by["level"]["start_refused"] or "Settings" in by["level"]["start_refused"]

    r = c.post("/api/reactors/R1/loops", json={"loops": ["ph", "temperature"], "on": True}).json()
    assert [x["status"] for x in r["results"]] == ["confirmed", "confirmed"]
    assert drivers["R1"].loops_active["ph"] and drivers["R1"].loops_active["temperature"]
    assert c.post("/api/reactors/R1/loops", json={"loops": ["do2"], "on": True}).status_code == 409   # not allowed
    assert not drivers["R1"].loops_active["do2"]

    # allow dO2 (lab PC only): starting it also starts the stirrer, no "external" alert for either
    assert c.put("/api/settings/loops", json={"allowed": {"R1": ["ph", "temperature", "stirrer", "do2"]}}).status_code == 200
    remote = TestClient(app, client=("100.101.102.103", 50000))
    assert remote.put("/api/settings/loops", json={"allowed": {"R1": ["ph"]}}).status_code == 403
    assert remote.post("/api/reactors/R1/loops", json={"loops": ["ph"], "on": False}).status_code == 403  # remote off by default
    app.state.acq.poll_once()
    assert c.post("/api/reactors/R1/loops", json={"loops": ["do2"], "on": True}).json()["results"][0]["status"] == "confirmed"
    assert drivers["R1"].loops_active["stirrer"]
    run_hours(app, clock, 0.05)
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    kinds = [x["kind"] for x in ev]
    assert kinds.count("loop_started") == 3 and "loop_switched_on" not in kinds
    assert any("in cascade" in x["message"] for x in ev)

    # someone stops the pH loop on the display: one alert
    drivers["R1"].loops_active["ph"] = False
    run_hours(app, clock, 0.05)
    ev = c.get(f"/api/experiments/{e['id']}/events").json()
    off = [x for x in ev if x["kind"] == "loop_switched_off"]
    assert len(off) == 1 and off[0]["severity"] == "alert" and "pH" in off[0]["message"]
    r = c.post("/api/reactors/R1/loops", json={"loops": ["temperature"], "on": False}).json()
    assert r["results"][0]["status"] == "confirmed" and not drivers["R1"].loops_active["temperature"]
    run_hours(app, clock, 0.05)
    assert not any(x["kind"] == "loop_switched_off" and "Temperature" in x["message"]
                   for x in c.get(f"/api/experiments/{e['id']}/events").json())
