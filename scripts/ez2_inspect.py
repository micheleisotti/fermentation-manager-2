"""
ez2-Control READ-ONLY inspection tool for Fermentation Manager 2.

It never logs in and never writes: every request it sends is a read ("J<address>S").

Usage (PowerShell, from C:\\FermentationManager):
  .venv\\Scripts\\python.exe scripts\\ez2_inspect.py dump  192.0.2.41
  .venv\\Scripts\\python.exe scripts\\ez2_inspect.py watch 192.0.2.41 1.14 1.19 2.12 2.13

dump : saves the controller's web-UI scripts and the values of every address that answers
       into data\\inspect\\<ip>-<date-time>\\  (values.tsv + web\\...)
sensor: every value of the given sensor indices (default 32 = Redox), e.g. sensor <ip> 32 1
web  : saves only the web-UI scripts into data\\inspect\\<ip>-web\\
watch: reads the given address families every second and prints only what changes
       (Ctrl+C to stop); also appends to data\\inspect\\watch-<ip>-<date>.log
"""
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from websockets.sync.client import connect

HERE = Path(__file__).resolve().parent.parent / "data" / "inspect"   # data/ is git-ignored
HERE.mkdir(parents=True, exist_ok=True)


def read_frame(addrs):
    req = "".join(f"J{a}S\r" for a in addrs)
    # safety: only uppercase-S read requests, nothing else
    assert all(line.startswith("J") and line.endswith("S") for line in req.split("\r") if line)
    return req.encode("ascii")


def parse(payload):
    text = payload.decode("latin-1") if isinstance(payload, (bytes, bytearray)) else payload
    out = {}
    for line in text.split("\r"):
        line = line.strip("\n")
        a, e = line.find("A"), line.find("E")
        if line.startswith("J") and a > 1 and (e < 0 or a < e):
            out[line[1:a]] = line[a + 1:]
    return out


class Link:
    def __init__(self, ip):
        self.url = f"ws://{ip}/"
        self.ws = None

    def query(self, addrs, quiet=0.6, limit=4.0):
        for attempt in (1, 2):
            try:
                if self.ws is None:
                    self.ws = connect(self.url, open_timeout=5, close_timeout=1, ping_interval=None,
                                      max_size=2 ** 22, compression=None).__enter__()
                self.ws.send(read_frame(addrs))
                got, want = {}, set(addrs)
                end = time.monotonic() + limit
                buf = ""
                while want - got.keys() and time.monotonic() < end:
                    try:
                        f = self.ws.recv(timeout=quiet)
                    except TimeoutError:
                        break
                    buf += f.decode("latin-1") if isinstance(f, (bytes, bytearray)) else f
                    head, _, buf = buf.rpartition("\r")
                    got.update({k: v for k, v in parse(head).items() if k in want})
                return got
            except Exception as e:
                try:
                    self.ws.close()
                except Exception:
                    pass
                self.ws = None
                if attempt == 2:
                    print("  read failed:", e)
                    return {}


def families():
    """Address families to scan: (prefix, index range)."""
    fam = [("0", range(0, 200))]
    for p in range(0, 100):
        fam.append((f"1.{p}", range(0, 32)))     # sensors / loops
    for p in range(0, 60):
        fam.append((f"2.{p}", range(0, 64)))     # actuators
    for p in range(0, 100):
        fam.append((f"3.{p}", range(0, 32)))     # loop parameters
    for top in range(4, 10):
        for p in range(0, 40):
            fam.append((f"{top}.{p}", range(0, 32)))
    return fam


KNOWN = """Scripts/myversion.js Scripts/controlsocket.js Scripts/data/data.js Scripts/data/datatools.js
Scripts/data/systemdata.js Scripts/data/sensordata.js Scripts/data/actuatordata.js Scripts/data/controldata.js
Scripts/data/lookup.js Scripts/navigationmanager.js Scripts/espfmanager.js Scripts/updatemanager.js
Scripts/toppanelmanager.js Scripts/overlaymanager.js Scripts/alarmmanager.js Scripts/usermanager.js
Scripts/rightpanel/rightpanelwindow.js Scripts/rightpanel/rightpaneloutput.js Scripts/rightpanel/rightpanelsensors.js
Scripts/rightpanel/rightpanelactuators.js Scripts/windows/windowmanager.js Scripts/windows/windowwithtabs.js
Scripts/windows/subwindow.js Scripts/windows/windowitems.js Scripts/windows/windowtools.js Scripts/windows/synoptic.js
Scripts/windows/login.js Scripts/windows/networksettings.js Scripts/windows/controllersetupstandard.js
Scripts/multireactor/multiuser.js Scripts/multireactor/multibioreactor.js Scripts/multireactor/login.js
Scripts/multireactor/cardview.js Scripts/multireactor/tableview.js Scripts/multireactor/trendview.js
Scripts/multireactor/bioreactormanager.js Scripts/main.js""".split()


def fetch_web(ip, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = f"http://{ip}/"
    seen, todo, ok = set(), ["", "index.html"] + KNOWN, 0
    while todo:
        rel = todo.pop(0)
        if rel in seen or len(seen) > 600:
            continue
        seen.add(rel)
        try:
            req = urllib.request.Request(base + rel, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            print("  skip", rel or "/", e)
            continue
        ok += 1
        (dest_dir / ((rel or "root.html").replace("/", "__"))).write_bytes(data)
        text = data.decode("latin-1")
        for m in re.findall(r"""((?:[A-Za-z_][\w-]*/)*[A-Za-z_][\w.-]*\.(?:js|html|htm))\b""", text):
            m = m.lstrip("/")
            if m.startswith(("libs/", "http")) or ".min." in m:
                continue
            if "/" not in m and not m.endswith(".html"):
                # bare file names inside a script folder: try the folders we know
                for folder in ("Scripts/", "Scripts/windows/", "Scripts/data/", "Scripts/rightpanel/", "Scripts/multireactor/"):
                    if folder + m not in seen:
                        todo.append(folder + m)
            elif m not in seen:
                todo.append(m)
    print(f"web files saved: {ok} (of {len(seen)} tried) in {dest_dir}")


def dump(ip):
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = HERE / f"{ip}-{stamp}"
    (out / "web").mkdir(parents=True, exist_ok=True)

    # 1) web-UI files (plain HTTP GET, as any browser does)
    fetch_web(ip, out / "web")

    # 2) every address that answers
    link = Link(ip)
    values = {}
    for prefix, idx in families():
        addrs = [f"{prefix}.{i}" for i in idx]
        for k in range(0, len(addrs), 32):
            values.update(link.query(addrs[k:k + 32], quiet=0.4, limit=3.0))
        print(f"\r{prefix:8s} answered so far: {len(values)}", end="", flush=True)
    print()

    def key(a):
        return [int(x) for x in a.split(".")]
    with open(out / "values.tsv", "w", encoding="utf-8") as fh:
        for a in sorted(values, key=key):
            fh.write(f"{a}\t{values[a]}\n")
    print(f"saved {len(values)} values in {out}")


def watch(ip, prefixes):
    link = Link(ip)
    addrs = []
    for p in prefixes:
        n = 64 if p.startswith("2.") else 32
        addrs += [f"{p}.{i}" for i in range(n)] if p.count(".") == 1 else [p]
    log = HERE / f"watch-{ip}-{datetime.now():%Y%m%d}.log"
    last = {}
    print(f"watching {len(addrs)} addresses, Ctrl+C to stop; log: {log}")
    with open(log, "a", encoding="utf-8") as fh:
        while True:
            t0 = time.monotonic()
            now = {}
            for k in range(0, len(addrs), 32):
                now.update(link.query(addrs[k:k + 32], quiet=0.3, limit=2.0))
            ts = datetime.now().strftime("%H:%M:%S")
            for a, v in now.items():
                if a in last and last[a] != v:
                    line = f"{ts}\t{a}\t{last[a]} -> {v}"
                    print(line)
                    fh.write(line + "\n")
                    fh.flush()
                last[a] = v
            time.sleep(max(0.0, 1.0 - (time.monotonic() - t0)))


def sensor(ip, idx):
    """All sensor (1.x), loop (3.x) and sensor-specific (5.idx.x) values of one sensor index."""
    link = Link(ip)
    addrs = [f"1.{p}.{idx}" for p in range(100)] + [f"3.{p}.{idx}" for p in range(100)] + \
            [f"5.{idx}.{p}" for p in range(40)]
    got = {}
    for k in range(0, len(addrs), 32):
        got.update(link.query(addrs[k:k + 32], quiet=0.4, limit=3.0))
    out = HERE / f"sensor{idx}-{ip}-{datetime.now():%Y%m%d-%H%M}.tsv"
    with open(out, "w", encoding="utf-8") as fh:
        for a in addrs:
            if a in got:
                fh.write(f"{a}\t{got[a]}\n")
                print(f"{a}\t{got[a]}")
    print(f"saved {len(got)} values in {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("dump", "watch", "web", "sensor"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "sensor":
        for i in sys.argv[3:] or ["32"]:
            sensor(sys.argv[2], int(i))
    elif sys.argv[1] == "web":
        fetch_web(sys.argv[2], HERE / f"{sys.argv[2]}-web")
    elif sys.argv[1] == "dump":
        dump(sys.argv[2])
    else:
        watch(sys.argv[2], sys.argv[3:] or ["1.14", "1.19", "2.12", "2.13"])
