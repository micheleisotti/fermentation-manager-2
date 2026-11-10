"""Settings page backend: stored settings, push notifications, backups."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from fm2.api.app import create_app
from fm2.config import Settings
from fm2.drivers.simulator import FakeClock, SimulatedController
from fm2.notify import NotifyError, send


@pytest.fixture()
def env(tmp_path):
    clock = FakeClock()
    drivers = {"R1": SimulatedController("R1", clock=clock, seed=1), "R2": SimulatedController("R2", clock=clock, seed=2)}
    app = create_app(Settings(data_dir=tmp_path), drivers=drivers, clock=clock, start_acquisition=False,
                     db_path=tmp_path / "t.db")
    return TestClient(app), clock, app, drivers


def test_settings_defaults_validation_and_live_apply(env):
    c, clock, app, drivers = env
    s = c.get("/api/settings").json()
    assert s["remote"]["ranges"]["ph"] == {"min": 4.0, "max": 8.0, "max_step": 1.0}
    bad = dict(s["remote"]); bad["ranges"] = {**s["remote"]["ranges"], "ph": {"min": 7, "max": 6, "max_step": 1}}
    assert c.put("/api/settings/remote", json=bad).status_code == 422
    ok = dict(s["remote"]); ok["ranges"] = {**s["remote"]["ranges"], "ph": {"min": 5.5, "max": 6.8, "max_step": 0.3}}
    assert c.put("/api/settings/remote", json=ok).status_code == 200
    # the new range applies at once to setpoint commands
    assert c.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.9}).status_code == 422
    assert c.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.5}).status_code == 422  # step 0.5 > 0.3
    assert c.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.2}).json()["status"] == "confirmed"
    # remote changes can be switched off
    ok["allow_remote_setpoints"] = False
    c.put("/api/settings/remote", json=ok)
    # remote vs local is decided from the connection (Tailscale address), not from what the browser says
    rc = TestClient(app, client=("100.101.102.103", 50000))
    assert rc.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.1, "origin": "local"}).status_code == 403
    assert rc.post("/api/reactors/R1/limits", json={"parameter": "ph", "low": 5.7}).status_code == 403
    assert c.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.1, "origin": "remote"}).status_code == 200
    # remote and backup settings can be changed only on the lab PC
    ok["allow_remote_setpoints"] = True
    assert rc.put("/api/settings/remote", json=ok).status_code == 403
    assert rc.put("/api/settings/backup", json=c.get("/api/settings").json()["backup"]).status_code == 403
    assert rc.get("/api/system").json()["client_local"] is False and c.get("/api/system").json()["client_local"] is True
    # through "tailscale serve" (HTTPS) requests come from 127.0.0.1 with forwarding headers: remote
    assert c.get("/api/system", headers={"X-Forwarded-For": "100.80.1.2"}).json()["client_local"] is False
    assert c.get("/api/system", headers={"Tailscale-User-Login": "someone@example.org"}).json()["client_local"] is False
    c.put("/api/settings/remote", json=ok)
    r = rc.post("/api/reactors/R1/setpoints", json={"parameter": "ph", "value": 6.0}).json()
    assert r["status"] == "confirmed"
    # alert thresholds apply to running experiments
    a = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{a['id']}/start")
    al = c.get("/api/settings").json()["alerts"]; al["silent_after_min"] = 12
    assert c.put("/api/settings/alerts", json=al).status_code == 200
    assert app.state.acq.alert_states[a["id"]].params.silent_after_s == 720
    # survives a restart (stored in the database)
    from fm2.services.app_settings import AppSettings
    assert AppSettings(app.state.sessions).get("alerts")["silent_after_min"] == 12


def test_secrets_are_masked(env):
    c, *_ = env
    n = c.get("/api/settings").json()["notifications"]
    n.update(channel="telegram", telegram_token="123456789:AAHsecretSECRETsecret_secret-SECRETsec", telegram_chat_id="42")
    out = c.put("/api/settings/notifications", json=n).json()
    assert out["telegram_token"] == "••••••" and out["telegram_chat_id"] == "42"
    # sending the mask back keeps the stored token
    out["telegram_chat_id"] = "43"
    c.put("/api/settings/notifications", json=out)
    assert c.app.state.app_settings.get("notifications")["telegram_token"] == "123456789:AAHsecretSECRETsecret_secret-SECRETsec"
    n2 = dict(out); n2.update(channel="ntfy", ntfy_topic="")
    assert c.put("/api/settings/notifications", json=n2).status_code == 422   # topic required


def test_send_formats():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    send({"channel": "ntfy", "ntfy_server": "https://ntfy.example", "ntfy_topic": "fm2-lab", "ntfy_token": ""},
         "FM2 alert · R1", "R1: pH 6.55 outside limits", "alert", client=client)
    assert str(seen[0].url) == "https://ntfy.example/fm2-lab" and seen[0].headers["Priority"] == "high"
    assert seen[0].content.decode() == "R1: pH 6.55 outside limits"
    send({"channel": "telegram", "telegram_token": "T", "telegram_chat_id": "42"}, "FM2", "hello", client=client)
    assert str(seen[1].url) == "https://api.telegram.org/botT/sendMessage"
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="no")))
    with pytest.raises(NotifyError):
        send({"channel": "telegram", "telegram_token": "T", "telegram_chat_id": "42"}, "a", "b", client=bad)
    with pytest.raises(NotifyError):
        send({"channel": "none"}, "a", "b")


def test_events_are_notified_by_rule(env):
    c, clock, app, drivers = env
    sent = []
    app.state.notifier._send = lambda cfg, title, msg, sev: sent.append((title, msg, sev))
    n = c.get("/api/settings").json()["notifications"]
    n.update(channel="ntfy", ntfy_topic="fm2-test")
    n["rules"]["silent"] = False
    c.put("/api/settings/notifications", json=n)
    a = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{a['id']}/start")              # pH 6.55 outside 5.8–6.3 at Start -> alert
    app.state.notifier.drain()
    assert any("outside limits at Start" in m for _, m, _ in sent) and sent[0][2] == "alert"
    sent.clear()
    drivers["R1"].fail_reads = True                            # controller silent: rule switched off
    from datetime import timedelta
    for _ in range(8):
        clock.advance(minutes=1); app.state.acq.poll_once()
    app.state.notifier.drain()
    assert not any("no reading" in m for _, m, _ in sent)
    assert any(e["kind"] == "controller_silent" for e in c.get(f"/api/experiments/{a['id']}/events").json())  # still stored


def test_backup(env, tmp_path):
    c, clock, app, drivers = env
    dest = tmp_path / "backups"
    assert c.post("/api/backup/run").status_code == 502                       # no destination yet
    b = c.get("/api/settings").json()["backup"]
    b.update(enabled=True, destination=str(tmp_path / "missing"))
    c.put("/api/settings/backup", json=b)
    r = c.post("/api/backup/run")
    assert r.status_code == 502 and "not found" in r.json()["detail"]
    dest.mkdir()
    b.update(destination=str(dest), keep=2)
    c.put("/api/settings/backup", json=b)
    a = c.post("/api/experiments", json={"name": "x", "reactor_id": "R1"}).json()
    c.post(f"/api/experiments/{a['id']}/start")
    for _ in range(3):
        clock.advance(minutes=10); app.state.acq.poll_once()
    files = []
    for i in range(3):
        clock.advance(seconds=1)
        app.state.backups.clock = lambda i=i: __import__("datetime").datetime(2026, 11, 9, 20, 0, i)
        files.append(c.post("/api/backup/run").json()["file"])
    kept = sorted(p.name for p in dest.glob("fm2-backup-*.db"))
    assert len(kept) == 2 and not list(dest.glob(".*partial"))                 # keep = 2
    con = sqlite3.connect(dest / kept[-1])
    assert con.execute("SELECT count(*) FROM controller_readings").fetchone()[0] >= 3
    con.close()
    assert (dest / "csv" / f"{a['code']}_controller.csv").read_text().count("\n") >= 4
    st = c.get("/api/backup/status").json()
    assert st["backups_present"] == 2 and st["last_error"] is None and st["db_bytes"] > 0


def test_system_status_parsing(env):
    from fm2 import system_status as ss
    ok = {"BackendState": "Running", "Self": {"DNSName": "lab-pc.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.5"]},
          "Peer": {"a": {"Online": True}, "b": {"Online": False}}}
    assert ss.parse_status(ok) == {"state": "available", "backend_state": "Running", "name": "lab-pc.tail1234.ts.net",
                                   "ips": ["100.64.0.5"], "devices_online": 1}
    assert ss.parse_status({"BackendState": "Stopped"})["state"] == "not_available"

    class R:
        stdout, stderr = "", "failed to connect to local tailscaled"
    ss._CACHE.update(at=0.0, value=None)
    no_ts = lambda *a, **k: [(2, 1, 6, "", ("192.0.2.10", 0))]
    assert ss.remote_access(runner=lambda *a, **k: R(), ttl=0, resolver=no_ts)["state"] == "unknown"
    with_ts = lambda *a, **k: [(2, 1, 6, "", ("192.0.2.10", 0)), (2, 1, 6, "", ("100.101.7.9", 0))]
    r = ss.remote_access(runner=lambda *a, **k: R(), ttl=0, resolver=with_ts)
    assert r["state"] == "available" and r["ips"] == ["100.101.7.9"]
    c, *_ = env
    st = c.get("/api/system/status").json()
    assert st["server"]["uptime_s"] >= 0 and st["remote"]["state"] in ("available", "not_available", "unknown")


def test_telegram_token_normalised_and_checked():
    from fm2.services.app_settings import normalize_telegram_token, _validate, SettingsError
    tok = "123456789:AAHfakeFAKEfake_fake-fakeFAKEfake12345"
    for raw in (tok, f" {tok} ", f"bot{tok}", f'"{tok}"', f"https://api.telegram.org/bot{tok}/getMe"):
        assert normalize_telegram_token(raw) == tok
    ok = _validate("notifications", {"channel": "telegram", "telegram_token": f"bot{tok}", "telegram_chat_id": "42"})
    assert ok["telegram_token"] == tok
    import pytest as _pt
    with _pt.raises(SettingsError):
        _validate("notifications", {"channel": "telegram", "telegram_token": "not-a-token", "telegram_chat_id": "42"})


def test_telegram_chats_skips_the_bot():
    import httpx
    from fm2.notify import telegram_chats
    data = {"ok": True, "result": [
        {"update_id": 1, "message": {"from": {"id": 555, "is_bot": False}, "chat": {"id": 555, "first_name": "Michele", "type": "private"}}},
        {"update_id": 2, "my_chat_member": {"chat": {"id": -1001, "title": "Lab group", "type": "supergroup"}}},
        {"update_id": 3, "message": {"chat": {"id": 555, "first_name": "Michele", "type": "private"}}},
    ]}
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=data)))
    chats = telegram_chats("123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", client)
    assert [c["id"] for c in chats] == ["555", "-1001"] and chats[0]["name"] == "Michele"
