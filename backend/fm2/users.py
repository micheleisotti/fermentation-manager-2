"""
Lightweight sign-in: who is using the app, with an optional numeric PIN per user.

Not an authorisation system: there are no roles. The server signs the token it hands
out (HMAC with a secret kept next to the database), so the name recorded on data comes
from the server, not from what the browser claims. Wrong PINs are rate limited.

Reset a forgotten PIN on the lab PC (admin shell):
    .venv\\Scripts\\python -m fm2.users reset-pin "Michele" --data-dir C:\\ProgramData\\FermentationManager\\data
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

PIN_MIN, PIN_MAX = 4, 8
TOKEN_DAYS = 180
MAX_FAILS, LOCK_S = 5, 300


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and PIN_MIN <= len(pin) <= PIN_MAX


def hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 200_000)
    return f"{salt.hex()}${h.hex()}"


def check_pin(pin: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(test.hex(), h)


def load_secret(path: Path) -> bytes:
    """Random secret created on first run (file readable by the service account only, like the data)."""
    if path.exists():
        return bytes.fromhex(path.read_text().strip())
    s = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s.hex())
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return s


def make_token(secret: bytes, user_id: int, version: int, now: Optional[float] = None) -> str:
    payload = f"{user_id}.{version}.{int((now or time.time()) + TOKEN_DAYS * 86400)}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).digest()[:18]
    return f"{payload}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def parse_token(secret: bytes, token: str, now: Optional[float] = None) -> Optional[tuple[int, int]]:
    """(user_id, version) for a valid, unexpired token; None otherwise."""
    try:
        uid, ver, exp, sig = token.split(".")
        payload = f"{uid}.{ver}.{exp}"
        good = base64.urlsafe_b64encode(hmac.new(secret, payload.encode(), hashlib.sha256).digest()[:18]).decode().rstrip("=")
        if not hmac.compare_digest(good, sig) or int(exp) < (now or time.time()):
            return None
        return int(uid), int(ver)
    except (ValueError, AttributeError):
        return None


class Throttle:
    """Per-user lock after repeated wrong PINs (in memory: reset on restart)."""

    def __init__(self):
        self._fails: dict[int, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, uid: int, now: Optional[float] = None) -> float:
        n, until = self._fails.get(uid, (0, 0.0))
        return max(0.0, until - (now or time.time())) if n >= MAX_FAILS else 0.0

    def fail(self, uid: int, now: Optional[float] = None) -> None:
        with self._lock:
            n, _ = self._fails.get(uid, (0, 0.0))
            n += 1
            self._fails[uid] = (n, (now or time.time()) + LOCK_S if n >= MAX_FAILS else 0.0)

    def ok(self, uid: int) -> None:
        with self._lock:
            self._fails.pop(uid, None)


def _cli() -> None:
    import argparse
    from sqlalchemy import select
    from .db import make_engine, make_session_factory, session_scope
    from .models import User

    ap = argparse.ArgumentParser(description="Fermentation Manager users")
    ap.add_argument("action", choices=["reset-pin", "list"])
    ap.add_argument("name", nargs="?")
    ap.add_argument("--data-dir", default=os.environ.get("FM2_DATA_DIR", "."))
    a = ap.parse_args()
    db = Path(a.data_dir) / "fermentation.db"
    if not db.exists():
        raise SystemExit(f"Database not found: {db}")
    with session_scope(make_session_factory(make_engine(db))) as s:
        if a.action == "list":
            for u in s.scalars(select(User)):
                print(f"{u.name}{'  (PIN)' if u.pin_hash else ''}{'  [archived]' if u.archived else ''}")
            return
        u = s.scalar(select(User).where(User.name == a.name))
        if u is None:
            raise SystemExit(f"No user named {a.name!r}")
        u.pin_hash, u.token_version = None, u.token_version + 1
        print(f"PIN removed for {u.name}: they can sign in without PIN and set a new one in Settings.")


if __name__ == "__main__":
    _cli()
