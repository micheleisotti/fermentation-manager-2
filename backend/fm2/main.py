"""Entry point: `fm2-server` (or `python -m fm2.main`)."""

from __future__ import annotations

import logging
import os

import uvicorn

from .api.app import create_app
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app(settings)


def run_server() -> None:
    # 0.0.0.0 so phones/laptops on the private VPN (Tailscale) can reach it.
    uvicorn.run(app, host=os.environ.get("FM2_HOST", "0.0.0.0"), port=int(os.environ.get("FM2_PORT", "8000")))


if __name__ == "__main__":
    run_server()
