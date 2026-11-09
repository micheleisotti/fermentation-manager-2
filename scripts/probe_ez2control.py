"""
Read-only probe of the ez2-Control controllers, using the same driver as the service.
Nothing is written to the controllers or to the database.

    .\\.venv\\Scripts\\python scripts\\probe_ez2control.py                 # R1-sx and R2-dx, 5 reads
    .\\.venv\\Scripts\\python scripts\\probe_ez2control.py 192.0.2.41 --n 20 --every 2
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fm2.drivers import DriverError  # noqa: E402
from fm2.drivers.ez2control import Ez2ControlDriver  # noqa: E402

DEFAULT = {"R1-sx": "192.0.2.42", "R2-dx": "192.0.2.41"}


def fmt(v, nd=2):
    return "—" if v is None else f"{v:.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ips", nargs="*", help="controller IPs (default: both reactors)")
    ap.add_argument("--n", type=int, default=5, help="number of reads")
    ap.add_argument("--every", type=float, default=5.0, help="seconds between reads")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    targets = {ip: ip for ip in args.ips} if args.ips else DEFAULT
    drivers = {name: Ez2ControlDriver(name, ip) for name, ip in targets.items()}
    print("time      reactor  pH (SP) [lim]            T °C (SP) [lim]          stir rpm (SP)  base mL  pump %  pH ctrl")
    try:
        for k in range(args.n):
            for name, d in drivers.items():
                t0 = time.monotonic()
                try:
                    s = d.read()
                except DriverError as e:
                    print(f"{time.strftime('%H:%M:%S')}  {name:7}  ERROR: {e}")
                    continue
                ms = (time.monotonic() - t0) * 1000
                pid = s.ph_control.pid or {}
                print(f"{time.strftime('%H:%M:%S')}  {name:7}  "
                      f"{fmt(s.ph)} ({fmt(s.ph_setpoint)}) [{fmt(s.ph_low_limit)}–{fmt(s.ph_high_limit)}]   "
                      f"{fmt(s.temperature,1)} ({fmt(s.temperature_setpoint,1)}) "
                      f"[{fmt(s.temperature_low_limit,1)}–{fmt(s.temperature_high_limit,1)}]   "
                      f"{fmt(s.stirrer,0)} ({fmt(s.stirrer_setpoint,0)})      "
                      f"{fmt(s.base_cumulative_ml)}    {fmt(s.base_pump_pct,0)}    "
                      f"{s.ph_control.mode} P{pid.get('P')} I{pid.get('I')} D{pid.get('D')}   ({ms:.0f} ms)")
            if k < args.n - 1:
                time.sleep(args.every)
    finally:
        for d in drivers.values():
            d.close()


if __name__ == "__main__":
    main()
