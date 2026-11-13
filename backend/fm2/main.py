"""Entry point: `fm2-server` (or `python -m fm2.main`)."""

from __future__ import annotations

import logging
import os

import uvicorn

from .api.app import create_app
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx logs every request URL at INFO: Telegram URLs contain the bot token, which must not end up in the log files
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def _clock():
    """Real clock, or an accelerated one for demos (FM2_SIM_SPEED > 1, simulator only)."""
    speed = float(os.environ.get("FM2_SIM_SPEED", "1"))
    if speed == 1:
        return None
    from datetime import datetime, timezone
    t0 = datetime.now(timezone.utc)
    return lambda: t0 + (datetime.now(timezone.utc) - t0) * speed


_c = _clock()
if _c is None:
    app = create_app(settings)
else:
    from .drivers import make_driver
    app = create_app(settings, clock=_c,
                     drivers={r.reactor_id: make_driver(r.driver, r.reactor_id, r.address, clock=_c) for r in settings.reactors})


def run_server() -> None:
    # 0.0.0.0 so phones/laptops on the private VPN (Tailscale) can reach it.
    uvicorn.run(app, host=os.environ.get("FM2_HOST", "0.0.0.0"), port=int(os.environ.get("FM2_PORT", "8000")))


if __name__ == "__main__":
    run_server()
