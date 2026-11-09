"""
Base-dosing metrics from the controller's cumulative volume.

The cumulative series is used as reported (already calibrated on the controller via
dose monitoring): no smoothing or fitting. Because doses are discrete, the rate in short
bins is "grainy" (0, 1, 2 doses per bin); 1-h bins show the trend more clearly.

Gaps (missing readings, e.g. lost connection) are handled explicitly: a bin is marked
incomplete instead of showing a fake drop in rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class RateBin:
    t_start_h: float
    t_end_h: float
    rate_ml_min: Optional[float]   # None when the bin lacks data
    complete: bool


def rate_bins(t_h, cum_ml, bin_h: float = 0.25, max_gap_h: Optional[float] = None,
              t_now_h: Optional[float] = None) -> list[RateBin]:
    """
    Mean base rate per bin = (cum at bin end - cum at bin start) / bin duration.
    Cumulative values at bin edges are linearly interpolated between readings, which
    is exact for a staircase sampled densely enough relative to the bin.
    A bin is incomplete if it extends past the last reading (current bin) or contains
    a gap longer than max_gap_h (default: one third of the bin).
    """
    t = np.asarray(t_h, dtype=float)
    c = np.asarray(cum_ml, dtype=float)
    m = np.isfinite(t) & np.isfinite(c)
    t, c = t[m], c[m]
    if len(t) < 2:
        return []
    o = np.argsort(t)
    t, c = t[o], c[o]
    c = np.maximum.accumulate(c)  # a counter never decreases; guards against read glitches
    max_gap = max_gap_h if max_gap_h is not None else bin_h / 3
    t_end = t[-1] if t_now_h is None else max(t_now_h, t[-1])
    out: list[RateBin] = []
    start = float(np.floor(t[0] / bin_h) * bin_h)
    while start < t_end - 1e-12:
        end = start + bin_h
        lo, hi = max(start, t[0]), min(end, t[-1])
        if hi <= lo:
            out.append(RateBin(start, end, None, False))
            start = end
            continue
        pts = np.concatenate(([lo], t[(t > lo) & (t < hi)], [hi]))
        gappy = bool(np.any(np.diff(pts) > max_gap))
        rate = None if gappy else float((np.interp(hi, t, c) - np.interp(lo, t, c)) / ((hi - lo) * 60))
        complete = bool((not gappy) and abs(lo - start) < 1e-9 and abs(hi - end) < 1e-9)
        out.append(RateBin(start, float(end), rate, complete))
        start = end
    return out


@dataclass
class BaseDosingSummary:
    total_ml: Optional[float]
    peak_rate_ml_min: Optional[float]
    t_peak_h: Optional[float]
    t_half_total_h: Optional[float]
    last_hour_rate_ml_min: Optional[float]
    n_doses: Optional[int]
    base_per_final_od: Optional[float] = None


def summarize(t_h, cum_ml, final_od: Optional[float] = None, bin_h: float = 0.25) -> BaseDosingSummary:
    t = np.asarray(t_h, dtype=float)
    c = np.asarray(cum_ml, dtype=float)
    m = np.isfinite(t) & np.isfinite(c)
    t, c = t[m], c[m]
    if len(t) < 2:
        return BaseDosingSummary(None, None, None, None, None, None)
    o = np.argsort(t)
    t, c = t[o], np.maximum.accumulate(c[o])
    total = float(c[-1] - c[0])
    bins = [b for b in rate_bins(t, c, bin_h) if b.complete and b.rate_ml_min is not None]
    peak = max(bins, key=lambda b: b.rate_ml_min) if bins else None
    half = None
    if total > 0:
        target = c[0] + total / 2
        idx = int(np.argmax(c >= target))
        half = float(t[idx])
    last_rate = None
    if t[-1] - t[0] >= 1.0:
        last_rate = float((c[-1] - np.interp(t[-1] - 1.0, t, c)) / 60.0)
    steps = int(np.sum(np.diff(c) > 1e-9))
    bpo = total / final_od if final_od and final_od > 0 else None
    return BaseDosingSummary(
        total_ml=total,
        peak_rate_ml_min=peak.rate_ml_min if peak else None,
        t_peak_h=(peak.t_start_h + peak.t_end_h) / 2 if peak else None,
        t_half_total_h=half, last_hour_rate_ml_min=last_rate,
        n_doses=steps, base_per_final_od=bpo,
    )
