"""
Single-experiment analysis: controller series averaged over an interval, manual
measurements grouped by time point, and publication figures (facets, ggplot-like themes).

Pure functions on arrays/DataFrames; the API module does the database work.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from . import base_dosing


@dataclass(frozen=True)
class SeriesDef:
    key: str
    label: str
    unit: str
    channel: str  # optional channel it belongs to (see drivers.base.CHANNELS)


# Controller series available in the analysis. "base_rate" is derived from the cumulative volume.
CONTROLLER_SERIES = [
    SeriesDef("ph", "pH", "", "ph"),
    SeriesDef("base_rate", "Base rate", "mL min⁻¹", "ph"),
    SeriesDef("base_cumulative_ml", "Base cumulative", "mL", "ph"),
    SeriesDef("base_pump_pct", "Base pump", "%", "ph"),
    SeriesDef("temperature", "T", "°C", "temperature"),
    SeriesDef("heating_pct", "Heating", "%", "temperature"),
    SeriesDef("cooling_pct", "Cooling", "%", "temperature"),
    SeriesDef("stirrer", "Stirrer", "rpm", "stirrer"),
    SeriesDef("do2", "dO2", "%", "do2"),
    SeriesDef("n2_pct", "N2 valve", "%", "do2"),
    SeriesDef("air_pct", "Air valve", "%", "do2"),
]
SERIES_BY_KEY = {s.key: s for s in CONTROLLER_SERIES}


def binned_series(df: pd.DataFrame, step_h: float, keys: list[str]) -> dict:
    """
    Controller data averaged per interval [k*step, (k+1)*step), plotted at the interval
    centre. The base rate is (cumulative at end - at start) / duration, None for intervals
    with a gap (see base_dosing.rate_bins); the cumulative volume is the value at the end
    of the interval. Intervals with no readings are omitted.
    """
    if df.empty or step_h <= 0:
        return {"t": [], **{k: [] for k in keys}}
    t = df["t_h"].astype(float).to_numpy()
    idx = np.floor(t / step_h + 1e-9).astype(int)
    g = df.assign(_bin=idx).groupby("_bin")
    bins = np.array(sorted(g.groups.keys()))
    out: dict = {"t": ((bins + 0.5) * step_h).tolist()}
    for k in keys:
        if k == "base_rate":
            rb = {int(round(b.t_start_h / step_h)): b.rate_ml_min for b in base_dosing.rate_bins(
                t, df["base_cumulative_ml"].astype(float).to_numpy(), step_h)}
            out[k] = [rb.get(int(b)) for b in bins]
        elif k not in df.columns:
            out[k] = [None] * len(bins)
        else:
            col = pd.to_numeric(df[k], errors="coerce")
            agg = (col.groupby(idx).max() if k == "base_cumulative_ml" else col.groupby(idx).mean()).reindex(bins)
            out[k] = [None if pd.isna(v) else float(v) for v in agg]
    return out


def grouped_points(t, y, decimals: int = 2) -> dict:
    """Manual readings at the same time point (rounded to 0.01 h) become mean ± SD (n)."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if not len(t):
        return {"t": [], "mean": [], "sd": [], "n": []}
    s = pd.DataFrame({"t": np.round(t, decimals), "y": y}).groupby("t")["y"]
    m, sd, n = s.mean(), s.std(ddof=1), s.count()
    return {"t": m.index.tolist(), "mean": m.tolist(),
            "sd": [None if (pd.isna(v) or c < 2) else float(v) for v, c in zip(sd, n)], "n": n.astype(int).tolist()}


# --- figures --------------------------------------------------------------------------------

THEMES = ("grey", "bw", "classic")
FIG_FORMATS = {"png": "image/png", "svg": "image/svg+xml", "pdf": "application/pdf"}


@dataclass
class Panel:
    title: str
    color: str
    points: Optional[dict] = None   # {"t", "mean", "sd"} manual points (with error bars if sd)
    curve: Optional[dict] = None    # {"t", "y"} fitted model
    line: Optional[dict] = None     # {"t", "y"} controller series
    step: bool = False


def render_figure(panels: list[Panel], fmt: str = "png", width_mm: float = 174, dpi: int = 300,
                  theme: str = "grey", ncols: int = 2, xlabel: str = "Time since start (h)") -> bytes:
    """Faceted time-course figure, free y scales, shared x axis label."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if fmt not in FIG_FORMATS:
        raise ValueError(f"Unknown format '{fmt}'")
    if theme not in THEMES:
        raise ValueError(f"Unknown theme '{theme}'")
    if not panels:
        raise ValueError("No panels to draw")
    ncols = max(1, min(ncols, len(panels)))
    nrows = int(np.ceil(len(panels) / ncols))
    w_in = width_mm / 25.4
    h_in = w_in * (0.36 if ncols > 1 else 0.6) * nrows + 0.35
    rc = {"font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
          "xtick.labelsize": 7, "ytick.labelsize": 7, "svg.fonttype": "none", "pdf.fonttype": 42}
    with plt.rc_context(rc):
        fig, axes = plt.subplots(nrows, ncols, figsize=(w_in, h_in), squeeze=False, sharex=True)
        t_max = max([max(p.points["t"], default=0) if p.points else 0 for p in panels]
                    + [max(p.line["t"], default=0) if p.line else 0 for p in panels]
                    + [max(p.curve["t"], default=0) if p.curve else 0 for p in panels] + [1])
        for i, ax in enumerate(axes.flat):
            if i >= len(panels):
                ax.set_visible(False)
                continue
            p = panels[i]
            _style(ax, theme)
            if p.line:
                ax.plot(p.line["t"], [np.nan if v is None else v for v in p.line["y"]], color=p.color, lw=0.9,
                        drawstyle="steps-mid" if p.step else "default")
            if p.curve:
                ax.plot(p.curve["t"], p.curve["y"], color=p.color, lw=0.9)
            if p.points and p.points["t"]:
                sd = [0 if s is None else s for s in p.points.get("sd") or []]
                if any(sd):
                    ax.errorbar(p.points["t"], p.points["mean"], yerr=sd, fmt="none", ecolor=p.color, elinewidth=0.7,
                                capsize=2)
                ax.plot(p.points["t"], p.points["mean"], "o", color=p.color, ms=3)
            ax.set_xlim(0, t_max * 1.02)
            if theme == "classic":
                ax.set_title(p.title, loc="left", pad=3)
            else:
                ax.set_title(p.title, pad=3, backgroundcolor="#d9d9d9" if theme == "grey" else "#f2f2f2")
            ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(5))
        fig.supxlabel(xlabel, fontsize=8)
        fig.tight_layout(pad=0.4, h_pad=0.6, w_pad=0.8)
        buf = io.BytesIO()
        fig.savefig(buf, format=fmt, dpi=dpi)
        plt.close(fig)
    return buf.getvalue()


def _style(ax, theme: str) -> None:
    for s in ax.spines.values():
        s.set_visible(theme != "grey")
        s.set_color("#333333" if theme == "classic" else "#7f7f7f")
        s.set_linewidth(0.6)
    if theme == "classic":
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(False)
    else:
        ax.set_facecolor("#ebebeb" if theme == "grey" else "white")
        ax.grid(True, color="white" if theme == "grey" else "#e5e5e5", lw=0.8)
        ax.set_axisbelow(True)
    ax.tick_params(length=2.5, width=0.5, colors="#4d4d4d")
