"""
Automatic backups of the database to a destination folder chosen on the Settings page.

- Uses SQLite's online backup API: a consistent copy even while acquisition is writing
  (never copy the live .db file with the file system).
- Writes to a temporary file in the destination, then renames it: an interrupted backup
  never leaves a half-written file with a valid name.
- Keeps the newest N backups; optionally also writes one CSV per experiment (readings and
  manual measurements), readable without this software.
- Runs every N hours, and optionally at each End of production.
"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .services.app_settings import AppSettings

log = logging.getLogger(__name__)
PREFIX = "fm2-backup-"


class BackupError(RuntimeError):
    pass


class BackupManager:
    def __init__(self, db_path: Path, settings: AppSettings,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.db_path = Path(db_path)
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_ok: Optional[datetime] = None
        self.last_file: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_attempt: Optional[datetime] = None

    # -- one backup -----------------------------------------------------------------
    def run(self, reason: str = "manual") -> Path:
        cfg = self.settings.get("backup")
        dest = Path(cfg["destination"]) if cfg["destination"] else None
        with self._lock:
            self.last_attempt = self.clock()
            try:
                if dest is None:
                    raise BackupError("no backup destination set")
                if not dest.is_dir():
                    raise BackupError(f"destination folder not found or not reachable: {dest}")
                stamp = self.clock().astimezone().strftime("%Y%m%d-%H%M%S")
                final = dest / f"{PREFIX}{stamp}.db"
                tmp = dest / f".{final.name}.partial"
                src = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                try:
                    out = sqlite3.connect(tmp)
                    try:
                        src.backup(out)
                        ok = out.execute("PRAGMA integrity_check").fetchone()[0]
                        if ok != "ok":
                            raise BackupError(f"integrity check of the copy failed: {ok}")
                    finally:
                        out.close()
                finally:
                    src.close()
                os.replace(tmp, final)
                if cfg["csv_per_experiment"]:
                    self._write_csv(dest / "csv")
                self._prune(dest, cfg["keep"])
                self.last_ok, self.last_file, self.last_error = self.clock(), str(final), None
                log.info("backup (%s) written to %s", reason, final)
                return final
            except (BackupError, OSError, sqlite3.Error) as e:
                self.last_error = str(e)
                log.error("backup (%s) failed: %s", reason, e)
                try:
                    if dest is not None:
                        for t in dest.glob(f".{PREFIX}*.partial"):
                            t.unlink(missing_ok=True)
                except OSError:
                    pass
                raise BackupError(str(e)) from e

    def _prune(self, dest: Path, keep: int) -> None:
        files = sorted(dest.glob(f"{PREFIX}*.db"))
        for f in files[:-keep] if keep > 0 else []:
            f.unlink(missing_ok=True)

    def _write_csv(self, folder: Path) -> None:
        """One file per experiment with controller readings, one with manual measurements."""
        folder.mkdir(exist_ok=True)
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            exps = con.execute("SELECT id, code FROM experiments WHERE started_at IS NOT NULL").fetchall()
            for exp_id, code in exps:
                for name, sql in (
                    (f"{code}_controller.csv",
                     "SELECT ts, t_h, ph, ph_setpoint, temperature, temperature_setpoint, stirrer, stirrer_setpoint, "
                     "base_cumulative_ml, base_pump_pct, ph_low_limit, ph_high_limit, temperature_low_limit, "
                     "temperature_high_limit, extra FROM controller_readings WHERE experiment_id = ? ORDER BY t_h"),
                    (f"{code}_manual.csv",
                     "SELECT m.sampled_at, m.t_h, v.name, m.value, m.unit, m.note, m.author FROM manual_measurements m "
                     "JOIN variables v ON v.id = m.variable_id WHERE m.experiment_id = ? ORDER BY m.t_h"),
                ):
                    cur = con.execute(sql, (exp_id,))
                    tmp = folder / f".{name}.partial"
                    with open(tmp, "w", newline="", encoding="utf-8") as fh:
                        w = csv.writer(fh)
                        w.writerow([d[0] for d in cur.description])
                        w.writerows(cur)
                    os.replace(tmp, folder / name)
        finally:
            con.close()

    # -- schedule -----------------------------------------------------------------------
    def due(self) -> bool:
        cfg = self.settings.get("backup")
        if not cfg["enabled"] or not cfg["destination"]:
            return False
        ref = self.last_ok or self.last_attempt
        return ref is None or (self.clock() - ref).total_seconds() >= cfg["every_h"] * 3600

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fm2-backup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        self._stop.wait(60)               # let the service settle after start
        while not self._stop.is_set():
            if self.due():
                try:
                    self.run("scheduled")
                except BackupError:
                    pass                  # recorded in last_error, shown on the Settings page
            self._stop.wait(300)

    def run_in_background(self, reason: str) -> None:
        threading.Thread(target=lambda: self._safe(reason), name="fm2-backup-once", daemon=True).start()

    def _safe(self, reason: str) -> None:
        try:
            self.run(reason)
        except BackupError:
            pass

    def status(self) -> dict:
        cfg = self.settings.get("backup")
        size = self.db_path.stat().st_size if self.db_path.exists() else None
        wal = Path(str(self.db_path) + "-wal")
        if size is not None and wal.exists():
            size += wal.stat().st_size
        files = []
        if cfg["destination"] and Path(cfg["destination"]).is_dir():
            files = sorted(p.name for p in Path(cfg["destination"]).glob(f"{PREFIX}*.db"))
        return {"enabled": cfg["enabled"], "destination": cfg["destination"],
                "last_ok": self.last_ok, "last_file": self.last_file, "last_error": self.last_error,
                "last_attempt": self.last_attempt, "db_bytes": size, "backups_present": len(files),
                "newest_backup": files[-1] if files else None}
