"""
Status of the bioreactor PC for the Home page: server uptime and remote access (Tailscale).

Tailscale is queried with its own CLI (`tailscale status --json`), cached for 30 s. When the
CLI is missing or does not answer, the remote-access state is "unknown" (not an error).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

STARTED = datetime.now(timezone.utc)
_CACHE: dict = {"at": 0.0, "value": None}
WINDOWS_PATHS = (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe")


def _tailscale_exe() -> Optional[str]:
    exe = shutil.which("tailscale")
    if exe:
        return exe
    return next((p for p in WINDOWS_PATHS if os.path.exists(p)), None)


def parse_status(data: dict) -> dict:
    me = data.get("Self") or {}
    state = data.get("BackendState", "")
    peers = [p for p in (data.get("Peer") or {}).values() if p.get("Online")]
    return {
        "state": "available" if state == "Running" else "not_available",
        "backend_state": state,
        "name": (me.get("DNSName") or "").rstrip("."),
        "ips": me.get("TailscaleIPs") or [],
        "devices_online": len(peers),
    }


def tailscale_addresses(resolver=None) -> list[str]:
    """Local IPv4 addresses in Tailscale's range 100.64.0.0/10 (works without CLI permissions)."""
    import ipaddress
    try:
        infos = (resolver or socket.getaddrinfo)(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    net = ipaddress.ip_network("100.64.0.0/10")
    return sorted({i[4][0] for i in infos if ipaddress.ip_address(i[4][0]) in net})


def remote_access(runner=subprocess.run, ttl: float = 30.0, resolver=None) -> dict:
    now = time.monotonic()
    if _CACHE["value"] is not None and now - _CACHE["at"] < ttl:
        return _CACHE["value"]
    exe = _tailscale_exe() if runner is subprocess.run else "tailscale"
    if exe is None:
        value = {"state": "unknown", "detail": "Tailscale CLI not found on this PC"}
    else:
        try:
            r = runner([exe, "status", "--json"], capture_output=True, text=True, timeout=5,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            value = parse_status(json.loads(r.stdout)) if r.stdout.strip() else \
                {"state": "unknown", "detail": (r.stderr or "no output").strip()[:200]}
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            value = {"state": "unknown", "detail": str(e)[:200]}
    if value["state"] == "unknown":
        # the service account may not be allowed to query Tailscale: fall back to its address
        ips = tailscale_addresses(resolver)
        if ips:
            value = {"state": "available", "ips": ips, "detail": "Tailscale address present on this PC"}
    _CACHE.update(at=now, value=value)
    return value


def server_status() -> dict:
    return {"host": socket.gethostname(), "started_at": STARTED,
            "uptime_s": (datetime.now(timezone.utc) - STARTED).total_seconds()}
