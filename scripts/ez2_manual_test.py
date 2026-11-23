"""
ez2-Control MANUAL-OUTPUT test for Fermentation Manager 2 (2.3.x preparation).

Writes the manual value 2.20.<actuator> exactly once ON and once OFF, logging every second what
the controller reports. It is the only FM2 script that writes 2.20, and only for the pump
actuators listed in PUMPS. It never claims a login: if someone is logged in on the display, it
reports the controller's answer and writes nothing.

Run from an ADMIN PowerShell (the password file is readable only by admins and the service),
in C:\\FermentationManager, with the FM2 service STOPPED or at least no experiment running:

  .venv\\Scripts\\python.exe scripts\\ez2_manual_test.py <controller-ip> base 2
  .venv\\Scripts\\python.exe scripts\\ez2_manual_test.py <controller-ip> base 2 --on-value 100
  .venv\\Scripts\\python.exe scripts\\ez2_manual_test.py <controller-ip> base 0 --leave-on   (cable test)
  .venv\\Scripts\\python.exe scripts\\ez2_manual_test.py <controller-ip> off base            (switch off)
  .venv\\Scripts\\python.exe scripts\\ez2_manual_test.py <controller-ip> login               (lock test)

Arguments: pump (base | acid | antifoam), seconds ON (max 10). Log written to
data\\inspect\\manual-<ip>-<date-time>.log : send that file.
"""
import argparse
import base64
import sys
import time
from datetime import datetime
from pathlib import Path

from websockets.sync.client import connect

PUMPS = {"base": 29, "acid": 28, "antifoam": 30}
LOOP_SENSOR_OF = {29: 0, 28: 0, 30: 3}          # pH loop for base/acid, Level loop for antifoam
PASSWORD_FILE = Path(r"C:\ProgramData\FermentationManager\ez2-password.txt")
OUT = Path(__file__).resolve().parent.parent / "data" / "inspect"
OUT.mkdir(parents=True, exist_ok=True)


def parse(payload):
    text = payload.decode("latin-1") if isinstance(payload, (bytes, bytearray)) else payload
    out = {}
    for line in text.split("\r"):
        if line.startswith("J") and "A" in line:
            a, _, v = line[1:].partition("A")
            if "E" not in a:
                out[a] = v
    return out


class Box:
    def __init__(self, ip, log):
        self.ws = connect(f"ws://{ip}/", open_timeout=5, close_timeout=1, max_size=None)
        self.log = log

    def say(self, msg):
        line = f"{datetime.now():%H:%M:%S.%f}"[:-3] + "  " + msg
        print(line)
        self.log.write(line + "\n")
        self.log.flush()

    def read(self, addrs, wait=2.0):
        self.ws.send("".join(f"J{a}S\r" for a in addrs).encode("ascii"))
        got, end = {}, time.time() + wait
        while time.time() < end and not all(a in got for a in addrs):
            try:
                got.update(parse(self.ws.recv(timeout=max(0.05, end - time.time()))))
            except TimeoutError:
                break
        return got

    def write(self, addr, value):
        assert addr.startswith(("2.20.", "0.11")), addr
        self.say(f"WRITE J{addr}s{value if not addr.startswith('0.11') else '<login>'}")
        self.ws.send(f"J{addr}s{value}\r".encode("ascii"))
        try:                                          # log any immediate answer (error text included)
            raw = self.ws.recv(timeout=1.0)
            self.say(f"  answer: {raw!r}"[:300])
        except TimeoutError:
            self.say("  answer: (none within 1 s)")

    def login(self, level="2"):
        pw = PASSWORD_FILE.read_text(encoding="utf-8").splitlines()[0].strip()
        before = self.read(["0.11"]).get("0.11", "")
        self.say(f"login state before: {before!r}")
        self.write("0.11", f"{level}|0|{base64.b64encode(pw.encode()).decode()}")
        after = self.read(["0.11"]).get("0.11", "")
        self.say(f"login state after:  {after!r}")
        return after.split("|")[0] in ("1", "2", "3")

    def logout(self):
        self.ws.send(b"J0.11s0|0|\r")
        self.say("logout sent")


def watch(box, a, s, seconds):
    keys = [f"2.20.{a}", f"2.49.{a}", f"2.12.{a}", f"2.13.{a}", f"2.47.{a}", f"1.14.{s}"]
    end = time.time() + seconds
    while time.time() < end:
        v = box.read(keys, wait=0.9)
        box.say("  ".join(f"{k}={v.get(k, '?')}" for k in keys))
        time.sleep(max(0.0, 1.0 - 0.9))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("ip")
    p.add_argument("what", help="base|acid|antifoam, or 'off', or 'login'")
    p.add_argument("arg", nargs="?", default="2")
    p.add_argument("--on-value", default="1")
    p.add_argument("--leave-on", action="store_true")
    p.add_argument("--level", default="2", choices=["1", "2"], help="1 Operator, 2 Engineer (FM2 default)")
    o = p.parse_args()

    log = open(OUT / f"manual-{o.ip}-{datetime.now():%Y%m%d-%H%M%S}.log", "w", encoding="utf-8")
    box = Box(o.ip, log)
    on_written = None
    try:
        if o.what == "login":
            ok = box.login(o.level)
            box.say("LOGIN OK" if ok else "LOGIN REFUSED")
            return
        if o.what == "off":
            a = PUMPS[o.arg]
            if box.login(o.level):
                box.write(f"2.20.{a}", "0")
                watch(box, a, LOOP_SENSOR_OF[a], 3)
            return
        a, s = PUMPS[o.what], LOOP_SENSOR_OF[PUMPS[o.what]]
        secs = min(float(o.arg), 10.0)
        box.say(f"pump {o.what} = actuator {a}; loop sensor {s}; ON for {secs:g} s, value {o.on_value!r}")
        watch(box, a, s, 2)
        if not box.login(o.level):
            box.say("LOGIN REFUSED: nothing written (is someone logged in on the display?)")
            return
        box.write(f"2.20.{a}", o.on_value)
        on_written = a
        watch(box, a, s, secs)
        if o.leave_on:
            on_written = None
            box.say("LEFT ON on purpose (--leave-on). Switch off with:  ... off " + o.what)
            return
        box.write(f"2.20.{a}", "0")
        on_written = None
        watch(box, a, s, 4)
    finally:
        if on_written is not None:                   # error or Ctrl+C while ON: switch off first
            try:
                box.write(f"2.20.{on_written}", "0")
            except Exception as e:
                print("COULD NOT SWITCH OFF - use the display!", e)
        try:
            box.logout()
        except Exception:
            pass
        log.close()


if __name__ == "__main__":
    sys.exit(main())
