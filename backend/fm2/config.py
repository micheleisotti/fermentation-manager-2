"""
Runtime configuration.

Values come from environment variables (prefix FM2_) so the same code runs on the
development laptop and on the bioreactor PC without edits. Defaults are safe for
local development: simulated controllers and a data folder next to the project.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"FM2_{name}", default)


@dataclass(frozen=True)
class ReactorConfig:
    reactor_id: str          # "R1", "R2"
    driver: str              # "simulator" | "ez2control"
    address: str = ""        # controller IP / URL for real drivers


@dataclass(frozen=True)
class SetpointRange:
    """Allowed range for remote setpoint changes, on top of the controller's own limits."""
    minimum: float
    maximum: float
    max_step: float          # largest change accepted in a single command


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(Path.cwd() / "data"))))
    frontend_dist: Path = field(default_factory=lambda: Path(_env(
        "FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))))
    sampling_interval_s: float = float(_env("SAMPLING_INTERVAL_S", "30"))
    reactors: tuple[ReactorConfig, ...] = (
        ReactorConfig("R1", _env("R1_DRIVER", "simulator"), _env("R1_ADDRESS", "")),
        ReactorConfig("R2", _env("R2_DRIVER", "simulator"), _env("R2_ADDRESS", "")),
    )
    # Placeholder ranges, to be agreed before going live (see Settings > Remote access).
    setpoint_ranges: dict[str, SetpointRange] = field(default_factory=lambda: {
        "ph": SetpointRange(4.0, 8.0, 1.0),
        "temperature": SetpointRange(10.0, 45.0, 25.0),
        "stirrer": SetpointRange(0.0, 1000.0, 300.0),
    })
    # Alert rule parameters (Settings > Notifications).
    alert_out_of_limits_after_s: float = 180.0
    alert_out_of_limits_rearm_s: float = 600.0
    alert_ph_tolerance: float = 0.05
    alert_temp_tolerance: float = 0.5
    alert_setpoint_hold_s: float = 120.0
    alert_silent_after_s: float = 300.0
    alert_finished_fraction_of_peak: float = 0.10
    alert_finished_hold_s: float = 1800.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "fermentation.db"


settings = Settings()
