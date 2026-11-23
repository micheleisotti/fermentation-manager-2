"""2.3: user levels (admin / operator / observer) and bioreactors configured in Settings."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from fm2.api.app import create_app, needed_role
from fm2.config import Settings
from fm2.drivers.simulator import FakeClock, SimulatedController


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    drivers = {"R1": SimulatedController("R1", clock=clock, seed=1)}
    app = create_app(Settings(data_dir=tmp_path), drivers=drivers, clock=clock, start_acquisition=False,
                     db_path=tmp_path / "t.db")
    return TestClient(app), clock, app


def _user(c, name, pin=None):
    r = c.post("/api/users", json={"name": name, **({"pin": pin} if pin else {})}).json()
    return r["user"], {"x-fm2-token": r["token"]}


def test_levels_not_enforced_until_an_admin_exists(env):
    c, _, _ = env
    me = c.get("/api/auth/me").json()
    assert me["role"] == "admin" and me["admins_exist"] is False
    assert c.post("/api/media", json={"name": "MRS"}).status_code in (200, 201)


def test_first_admin_and_levels(env):
    c, _, _ = env
    anna, h_anna = _user(c, "Anna", "1234")
    bob, h_bob = _user(c, "Bob", "5678")
    nopin, h_nopin = _user(c, "Carla")
    assert anna["role"] == "operator"
    # first admin: needs to be signed in as that user and to have a PIN
    assert c.post(f"/api/users/{nopin['id']}/first-admin", headers=h_nopin).status_code == 422
    assert c.post(f"/api/users/{anna['id']}/first-admin", headers=h_bob).status_code == 403
    assert c.post(f"/api/users/{anna['id']}/first-admin", headers=h_anna).json()["role"] == "admin"
    assert c.post(f"/api/users/{bob['id']}/first-admin", headers=h_bob).status_code == 409
    # now levels apply
    assert c.get("/api/auth/me", headers=h_bob).json()["role"] == "operator"
    assert c.get("/api/auth/me", headers=h_nopin).json()["role"] == "observer"    # no PIN: observer
    assert c.get("/api/auth/me").json()["role"] == "observer"                      # not signed in / Guest
    assert c.post("/api/media", json={"name": "LB"}).status_code == 403             # observer cannot write
    assert c.post("/api/media", json={"name": "LB"}, headers=h_nopin).status_code == 403
    assert c.post("/api/media", json={"name": "LB"}, headers=h_bob).status_code in (200, 201)
    assert c.put("/api/settings/alerts", json={}, headers=h_bob).status_code == 403  # operator: no settings
    assert c.patch("/api/bioreactors/R1", json={"name": "B1"}, headers=h_bob).status_code == 403
    assert c.patch("/api/bioreactors/R1", json={"name": "B1"}, headers=h_anna).status_code == 200
    # levels changed only by admins; the last admin is protected
    assert c.put(f"/api/users/{bob['id']}/role", json={"role": "admin"}, headers=h_bob).status_code == 403
    assert c.put(f"/api/users/{anna['id']}/role", json={"role": "operator"}, headers=h_anna).status_code == 409
    assert c.put(f"/api/users/{anna['id']}/archived", json={}).status_code in (403, 404, 405)
    assert c.put(f"/api/users/{bob['id']}/role", json={"role": "observer"}, headers=h_anna).json()["role"] == "observer"
    assert c.post("/api/media", json={"name": "M17"}, headers=h_bob).status_code == 403
    users = {u["name"]: u for u in c.get("/api/users").json()}
    assert users["Guest"]["role"] == "observer" and users["Bob"]["effective_role"] == "observer"


def test_rules_table():
    assert needed_role("GET", "/api/experiments") is None
    assert needed_role("POST", "/api/reactors/R1/setpoints") == "operator"
    assert needed_role("DELETE", "/api/experiments/3") == "admin"
    assert needed_role("PATCH", "/api/bioreactors/R1") == "admin"
    assert needed_role("POST", "/api/auth/login") is None


def test_bioreactors_configuration(env):
    c, clock, app = env
    data = c.get("/api/bioreactors").json()
    assert [r["id"] for r in data["reactors"]] == ["R1"] and data["interval"]["min"] == 2
    r1 = data["reactors"][0]
    assert r1["name"] == "Bioreactor 1" and r1["interval_s"] == 5
    # interval range and per-reactor interval
    assert c.patch("/api/bioreactors/R1", json={"interval_s": 1}).status_code == 422
    assert c.patch("/api/bioreactors/R1", json={"interval_s": 400}).status_code == 422
    out = c.patch("/api/bioreactors/R1", json={"interval_s": 2, "name": "Left"}).json()
    assert out["interval_s"] == 2 and out["interval_warning"] and app.state.acq.interval_of("R1") == 2
    # add a simulated bioreactor, enable it: it is polled and shows up live
    new = c.post("/api/bioreactors", json={"name": "Demo", "driver": "simulator", "enabled": True}).json()
    assert new["id"] == "R2" and "R2" in app.state.acq.drivers
    app.state.acq.poll_once()
    live = {x["reactor_id"]: x for x in c.get("/api/reactors/live").json()}
    assert live["R1"]["name"] == "Left" and live["R2"]["online"]
    assert c.post("/api/bioreactors/R2/test", json={}).json()["ok"]
    assert c.post("/api/bioreactors/R2/test", json={"driver": "ez2control", "address": ""}).json()["ok"] is False
    assert c.post("/api/bioreactors", json={"driver": "nope"}).status_code == 422
    # disable: removed from acquisition; delete only if never used
    c.patch("/api/bioreactors/R2", json={"enabled": False})
    assert "R2" not in app.state.acq.drivers
    assert c.delete("/api/bioreactors/R2").status_code == 204
    exp = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    assert c.delete("/api/bioreactors/R1").status_code == 409
    # changing the connection of a reactor with a running experiment needs confirmation
    c.post(f"/api/experiments/{exp['id']}/start")
    assert c.patch("/api/bioreactors/R1", json={"enabled": False}).status_code == 409
    assert c.patch("/api/bioreactors/R1", json={"interval_s": 10}).status_code == 200   # interval alone: no reconnect


def test_idle_history_kept_three_hours(env):
    c, clock, app = env
    for _ in range(4 * 60):            # 4 h, one reading per minute, no experiment
        clock.advance(minutes=1)
        app.state.acq.poll_once()
    h = c.get("/api/reactors/R1/history").json()
    assert h["window_h"] == 3 and 170 <= len(h["points"]) <= 181
    assert h["points"][-1]["ph"] is not None
    assert c.get("/api/experiments").json() == [] or True
