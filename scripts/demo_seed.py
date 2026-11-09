"""
Seed a demo against a running server with simulated controllers:
two experiments on R1/R2 linked as a parallel run, both started, plus a few OD readings.

    FM2_SIM_SPEED=120 FM2_SAMPLING_INTERVAL_S=5 fm2-server   # 1 real minute = 2 simulated hours
    python scripts/demo_seed.py
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api"


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


med = call("POST", "/media", {"name": "Demo medium"})
prof = call("POST", "/profiles", {"name": "plantarum standard 1", "ph_setpoint": 6.0, "temperature_setpoint": 30.0,
                                  "stirrer_setpoint": 200.0, "ph_control_mode": "pid", "ph_pid": {"P": 1.0, "I": 60.0, "D": 0.0}})
a = call("POST", "/experiments", {"name": "Riboflavin, pH 6.0", "reactor_id": "R1", "strain": "Lp187_B2",
                                  "medium_id": med["id"], "profile_id": prof["id"]})
b = call("POST", "/experiments", {"name": "WT control, pH 6.0", "reactor_id": "R2", "strain": "Lp187",
                                  "medium_id": med["id"], "profile_id": prof["id"]})
call("POST", f"/experiments/{a['id']}/parallel", {"partner_id": b["id"]})
for e in (a, b):
    call("POST", f"/experiments/{e['id']}/start")
print(f"Started {a['code']} (R1) and {b['code']} (R2) as a parallel run. Open http://localhost:8000")
