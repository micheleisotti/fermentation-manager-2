"""
Base-dosing metrics from the controller's cumulative volume.

The cumulative series is used as reported (already calibrated on the controller via
dose monitoring): no smoothing or fitting. Because doses are discrete, the rate in short
bins is "grainy" (0, 1, 2 doses per bin); 1-h bins show the trend more clearly.

Gaps (missing readings, e.g. lost connection) are handled explicitly: a bin is marked
incomplete instead of showing a fake drop in rate.

Counter resets: the controller's dose counter can go back to 0 (power cycle, reset on the
display; observed between 9 and 10 Nov 2026: 1.61 -> 0.00 mL). A drop larger than
RESET_TOL_ML is treated as a reset and the series is continued ("stitched"): the value
before the drop is added to every later reading. Doses between the last reading before
the reset and the reset itself cannot be known (at most one polling interval).
Smaller drops are read glitches and are flattened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


RESET_TOL_ML = 0.05   # counter has 2 decimals; anything below this is a read glitch


def stitch_counter(cum_ml) -> np.ndarray:
    """
    Continue a cumulative counter across resets. Non-finite values stay non-finite and
    do not break the series. Returns a float array of the same length.
    """
    c = np.asarray(cum_ml, dtype=float)
    out = np.full(c.shape, np.nan)
    offset, last = 0.0, None
    for i, v in enumerate(c):
        if not np.isfinite(v):
            continue
        if last is not None and v < last - RESET_TOL_ML:
            offset += last
        last = v
        out[i] = v + offset
    return out


def count_resets(cum_ml) -> int:
    c = np.asarray(cum_ml, dtype=float)
    c = c[np.isfinite(c)]
    return int(np.sum(np.diff(c) < -RESET_TOL_ML)) if len(c) > 1 else 0


class CounterStitcher:
    """Same rule as stitch_counter(), one value at a time (for live alert rules)."""

    def __init__(self) -> None:
        self.offset = 0.0
        self.last: Optional[float] = None

    def update(self, raw: float) -> tuple[float, bool]:
        """Returns (continued value, True if a reset was detected at this reading)."""
        reset = self.last is not None and raw < self.last - RESET_TOL_ML
        if reset:
            self.offset += self.last
        self.last = raw
        return raw + self.offset, reset


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
    t, c = t[o], stitch_counter(c[o])
    c = np.maximum.accumulate(c)  # a counter never decreases; guards against read glitches
    # A gap is "too long" relative to both the bin and the sampling interval actually used.
    max_gap = max_gap_h if max_gap_h is not None else max(bin_h / 3, 3 * float(np.median(np.diff(t))))
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
    t, c = t[o], np.maximum.accumulate(stitch_counter(c[o]))
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


@dataclass
class EstimatedEnd:
    t_end_h: Optional[float]        # when the base rate fell (for good) below the threshold
    t_notified_h: Optional[float]   # when the "probably finished" notification fires (t_end + hold)
    fraction: float
    hold_h: float
    window_h: float


def estimate_end(t_h, cum_ml, fraction: float = 0.10, hold_h: float = 0.5, window_h: float = 0.25) -> EstimatedEnd:
    """
    The rule of the "fermentation probably finished" notification (alerts._finished_rule), applied to
    the recorded series: base rate over the trailing `window_h` (only once a full window is available),
    running peak; the end is the first time the rate drops below `fraction` × peak and stays below for
    `hold_h`. The notification fires `hold_h` later. None when the rule never triggered.
    """
    out = EstimatedEnd(None, None, fraction, hold_h, window_h)
    t = np.asarray(t_h, dtype=float)
    c = np.asarray(cum_ml, dtype=float)
    m = np.isfinite(t) & np.isfinite(c) & (t >= 0)
    t, c = t[m], c[m]
    if len(t) < 3:
        return out
    o = np.argsort(t)
    t, c = t[o], np.maximum.accumulate(stitch_counter(c[o]))
    full = t - t[0] >= window_h * 0.9
    c_before = np.interp(t - window_h, t, c)
    span_h = np.minimum(window_h, t - t[0])
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(full & (span_h > 0), (c - c_before) / (span_h * 60.0), np.nan)
    peak = np.fmax.accumulate(np.where(np.isfinite(rate), rate, 0.0))
    low = np.isfinite(rate) & (peak > 0) & (rate < fraction * peak)
    i = 0
    while i < len(t):
        if not low[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(t) and low[j + 1]:
            j += 1
        if t[j] - t[i] >= hold_h:
            out.t_end_h, out.t_notified_h = float(t[i]), float(t[i] + hold_h)
            return out
        i = j + 1
    return out
