"""
Statistics for analysis sessions, ported from v1 (stats_batch.py) and extended.

- Replicate curves: mean ± SD on a common time grid, restricted to the overlap of the
  replicates' time ranges (no extrapolation). v2 adds a fixed-step grid (1/2/4 h) for
  the ggplot-style "points with error bars" figure.
- Parameter comparison: Welch's t-test for 2 conditions (with the difference and its
  95 % CI, Welch–Satterthwaite df), one-way ANOVA + Tukey HSD for 3+.
- With fewer than 2 replicates per condition no SD or test is computed: the result
  says so explicitly instead of inventing a value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats as st


@dataclass
class ReplicateCurve:
    t: np.ndarray
    mean: np.ndarray
    sd: np.ndarray
    n: int
    warning: Optional[str] = None


def replicate_curve(curves: list[tuple], step_h: Optional[float] = None, n_points: int = 50) -> Optional[ReplicateCurve]:
    """curves: list of (t, y). step_h: fixed grid spacing (e.g. 2.0) or None for n_points."""
    valid = [(np.asarray(t, float), np.asarray(y, float)) for t, y in curves if len(t) >= 2]
    if not valid:
        return None
    if len(valid) == 1:
        t, y = valid[0]
        if step_h:
            g = np.arange(np.ceil(t.min() / step_h) * step_h, t.max() + 1e-9, step_h)
            y = np.interp(g, t, y)
            t = g
        return ReplicateCurve(t, y, np.full_like(y, np.nan), 1, "Only one experiment: no standard deviation.")
    t_min = max(t.min() for t, _ in valid)
    t_max = min(t.max() for t, _ in valid)
    if t_min >= t_max:
        return ReplicateCurve(np.array([]), np.array([]), np.array([]), len(valid),
                              "Replicate time ranges do not overlap: no common mean curve.")
    grid = (np.arange(np.ceil(t_min / step_h) * step_h, t_max + 1e-9, step_h) if step_h
            else np.linspace(t_min, t_max, n_points))
    Y = np.vstack([np.interp(grid, t, y) for t, y in valid])
    return ReplicateCurve(grid, Y.mean(axis=0), Y.std(axis=0, ddof=1), len(valid))


@dataclass
class ConditionSummary:
    n: int
    mean: Optional[float]
    sd: Optional[float]


@dataclass
class Comparison:
    test: str
    statistic: Optional[float]
    p_value: Optional[float]
    conditions: dict[str, ConditionSummary]
    difference: Optional[float] = None          # first minus second (2 conditions)
    ci95: Optional[tuple[float, float]] = None
    posthoc: list[dict] = field(default_factory=list)
    warning: Optional[str] = None


def _summ(v: list[float]) -> ConditionSummary:
    a = np.asarray(v, float)
    return ConditionSummary(len(a), float(a.mean()) if len(a) else None,
                            float(a.std(ddof=1)) if len(a) >= 2 else None)


def compare_conditions(values: dict[str, list[float]]) -> Comparison:
    clean = {g: [float(x) for x in v if x is not None and np.isfinite(x)] for g, v in values.items()}
    summ = {g: _summ(v) for g, v in clean.items()}
    usable = {g: v for g, v in clean.items() if len(v) >= 2}
    if len(usable) < 2:
        return Comparison("none", None, None, summ,
                          warning="At least 2 conditions with at least 2 replicates each are needed for a test.")
    if len(usable) == 2:
        (g1, a), (g2, b) = usable.items()
        a, b = np.asarray(a), np.asarray(b)
        res = st.ttest_ind(a, b, equal_var=False)
        va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
        se = np.sqrt(va + vb)
        df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1)) if se > 0 else np.nan
        diff = float(a.mean() - b.mean())
        half = float(st.t.ppf(0.975, df) * se) if se > 0 else float("nan")
        warn = "Low power with n < 4 per condition: read p together with the difference and its CI." \
            if min(len(a), len(b)) < 4 else None
        return Comparison(f"Welch t-test ({g1} vs {g2})", float(res.statistic), float(res.pvalue), summ,
                          difference=diff, ci95=(diff - half, diff + half), warning=warn)
    groups = list(usable)
    arrays = [np.asarray(usable[g]) for g in groups]
    f = st.f_oneway(*arrays)
    tk = st.tukey_hsd(*arrays)
    ci = tk.confidence_interval(0.95)
    post = []
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            post.append({"a": groups[i], "b": groups[j],
                         "difference": float(arrays[i].mean() - arrays[j].mean()),
                         "p_adj": float(tk.pvalue[i, j]),
                         "ci95": (float(ci.low[i, j]), float(ci.high[i, j]))})
    return Comparison(f"One-way ANOVA ({len(groups)} conditions) + Tukey HSD", float(f.statistic), float(f.pvalue),
                      summ, posthoc=post)
