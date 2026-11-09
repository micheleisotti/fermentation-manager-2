"""
Settings edited from the Settings page, stored in the database (table app_settings) on top of
the defaults below. Each section is validated on save and applied immediately (no restart).
"""

from __future__ import annotations

import re

import copy
import threading
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session, sessionmaker

from ..alerts import AlertParams
from ..config import SetpointRange
from ..db import session_scope
from ..models import AppSetting

SECRET = "••••••"          # shown instead of stored secrets; sending it back keeps the old value

# Kinds of events, grouped into the notification rules of the Settings page
NOTIFY_RULES = {
    "limits": ("out_of_limits", "out_of_limits_at_start"),
    "setpoint_ph": ("setpoint_reached", "already_at_setpoint"),        # pH: message mentions pH
    "setpoint_t": ("setpoint_reached", "already_at_setpoint"),         # T: the others
    "silent": ("controller_silent", "controller_restored"),
    "external": ("external_setpoint_change",),
    "finished": ("probably_finished",),
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "remote": {
        "allow_remote_setpoints": True,
        "ranges": {   # allowed range and max step for setpoint changes from this software
            "ph": {"min": 4.0, "max": 8.0, "max_step": 1.0},
            "temperature": {"min": 10.0, "max": 45.0, "max_step": 25.0},
            "stirrer": {"min": 0.0, "max": 1000.0, "max_step": 300.0},
        },
    },
    "alerts": {
        "out_of_limits_after_min": 3.0, "rearm_min": 10.0, "ph_tolerance": 0.05, "temp_tolerance": 0.5,
        "hold_min": 2.0, "silent_after_min": 5.0, "finished_fraction_pct": 10.0, "finished_hold_min": 30.0,
    },
    "notifications": {
        "channel": "none",                       # "none" | "ntfy" | "telegram"
        "ntfy_server": "https://ntfy.sh", "ntfy_topic": "", "ntfy_token": "",
        "telegram_token": "", "telegram_chat_id": "",
        "rules": {"limits": True, "setpoint_ph": True, "setpoint_t": True, "silent": True,
                  "external": True, "finished": True},
    },
    "backup": {
        "enabled": False, "destination": "", "every_h": 24, "keep": 14,
        "on_end_of_production": True, "csv_per_experiment": True,
    },
}
SECRETS = {"notifications": ("telegram_token", "ntfy_token")}


def normalize_telegram_token(t: str) -> str:
    """Accept common paste mistakes: quotes, spaces, a 'bot' prefix or the whole API URL."""
    t = t.strip().strip("'\"").replace(" ", "")
    m = re.search(r"api\.telegram\.org/bot([^/]+)", t)
    if m:
        t = m.group(1)
    if t.lower().startswith("bot") and ":" in t:
        t = t[3:]
    return t


class SettingsError(ValueError):
    pass


def _num(d: dict, key: str, lo: float, hi: float) -> float:
    try:
        v = float(d[key])
    except (KeyError, TypeError, ValueError):
        raise SettingsError(f"{key}: a number is required")
    if not (lo <= v <= hi):
        raise SettingsError(f"{key}: {v:g} is outside {lo:g}–{hi:g}")
    return v


def _validate(section: str, v: dict) -> dict:
    if section == "remote":
        out = {"allow_remote_setpoints": bool(v.get("allow_remote_setpoints", True)), "ranges": {}}
        bounds = {"ph": (0, 14), "temperature": (0, 100), "stirrer": (0, 5000)}
        for p, (lo, hi) in bounds.items():
            r = (v.get("ranges") or {}).get(p) or {}
            mn, mx, st = _num(r, "min", lo, hi), _num(r, "max", lo, hi), _num(r, "max_step", 0, hi)
            if mn >= mx:
                raise SettingsError(f"{p}: min must be below max")
            if st <= 0:
                raise SettingsError(f"{p}: max step must be above 0")
            out["ranges"][p] = {"min": mn, "max": mx, "max_step": st}
        return out
    if section == "alerts":
        return {
            "out_of_limits_after_min": _num(v, "out_of_limits_after_min", 0, 120),
            "rearm_min": _num(v, "rearm_min", 0, 240),
            "ph_tolerance": _num(v, "ph_tolerance", 0.001, 2),
            "temp_tolerance": _num(v, "temp_tolerance", 0.01, 10),
            "hold_min": _num(v, "hold_min", 0, 120),
            "silent_after_min": _num(v, "silent_after_min", 1, 240),
            "finished_fraction_pct": _num(v, "finished_fraction_pct", 1, 90),
            "finished_hold_min": _num(v, "finished_hold_min", 5, 720),
        }
    if section == "notifications":
        ch = v.get("channel", "none")
        if ch not in ("none", "ntfy", "telegram"):
            raise SettingsError("channel must be none, ntfy or telegram")
        out = {k: str(v.get(k, "") or "").strip() for k in
               ("ntfy_server", "ntfy_topic", "ntfy_token", "telegram_token", "telegram_chat_id")}
        out["channel"] = ch
        out["ntfy_server"] = (out["ntfy_server"] or "https://ntfy.sh").rstrip("/")
        if ch == "ntfy" and not out["ntfy_topic"]:
            raise SettingsError("ntfy: a topic is required")
        out["telegram_token"] = normalize_telegram_token(out["telegram_token"])
        if ch == "telegram" and not (out["telegram_token"] and out["telegram_chat_id"]):
            raise SettingsError("Telegram: bot token and chat id are required")
        if out["telegram_token"] and not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", out["telegram_token"]):
            raise SettingsError("Telegram: the bot token should look like 123456789:AAH… (as given by @BotFather)")
        rules = v.get("rules") or {}
        out["rules"] = {k: bool(rules.get(k, True)) for k in NOTIFY_RULES}
        return out
    if section == "backup":
        every = int(_num(v, "every_h", 1, 168))
        keep = int(_num(v, "keep", 1, 1000))
        dest = str(v.get("destination", "") or "").strip()
        enabled = bool(v.get("enabled", False))
        if enabled and not dest:
            raise SettingsError("Choose a backup destination before enabling automatic backups")
        return {"enabled": enabled, "destination": dest, "every_h": every, "keep": keep,
                "on_end_of_production": bool(v.get("on_end_of_production", True)),
                "csv_per_experiment": bool(v.get("csv_per_experiment", True))}
    raise SettingsError(f"unknown settings section {section!r}")


class AppSettings:
    """Cached settings with listeners; thread-safe."""

    def __init__(self, sessions: sessionmaker[Session]):
        self._sessions = sessions
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULTS)
        self._listeners: list[Callable[[str, dict], None]] = []
        with session_scope(sessions) as s:
            for row in s.query(AppSetting).all():
                if row.key in self._data:
                    self._data[row.key] = {**self._data[row.key], **row.value}

    def on_change(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def get(self, section: str) -> dict:
        with self._lock:
            return copy.deepcopy(self._data[section])

    def public(self) -> dict:
        """All sections, with secrets masked."""
        out = {k: self.get(k) for k in DEFAULTS}
        for sec, keys in SECRETS.items():
            for k in keys:
                if out[sec].get(k):
                    out[sec][k] = SECRET
        return out

    def put(self, section: str, value: dict) -> dict:
        if section not in DEFAULTS:
            raise SettingsError(f"unknown settings section {section!r}")
        value = dict(value)
        for k in SECRETS.get(section, ()):               # masked secret sent back: keep the stored one
            if value.get(k) == SECRET:
                value[k] = self.get(section).get(k, "")
        clean = _validate(section, value)
        with session_scope(self._sessions) as s:
            row = s.get(AppSetting, section)
            if row is None:
                s.add(AppSetting(key=section, value=clean))
            else:
                row.value = clean
        with self._lock:
            self._data[section] = clean
        for fn in self._listeners:
            fn(section, clean)
        return clean

    # -- typed views used by the rest of the backend ------------------------------------
    def setpoint_ranges(self) -> dict[str, SetpointRange]:
        r = self.get("remote")["ranges"]
        return {p: SetpointRange(x["min"], x["max"], x["max_step"]) for p, x in r.items()}

    def alert_params(self) -> AlertParams:
        a = self.get("alerts")
        return AlertParams(out_of_limits_after_s=a["out_of_limits_after_min"] * 60, rearm_s=a["rearm_min"] * 60,
                           ph_tolerance=a["ph_tolerance"], temp_tolerance=a["temp_tolerance"],
                           hold_s=a["hold_min"] * 60, silent_after_s=a["silent_after_min"] * 60,
                           finished_fraction=a["finished_fraction_pct"] / 100.0,
                           finished_hold_s=a["finished_hold_min"] * 60)


def rule_of(kind: str, message: str) -> Optional[str]:
    """Notification rule an event belongs to."""
    if kind in ("setpoint_reached", "already_at_setpoint"):
        return "setpoint_ph" if ": pH" in message else "setpoint_t"
    for rule, kinds in NOTIFY_RULES.items():
        if kind in kinds:
            return rule
    return None
