"""
Growth kinetics and integral KPIs, ported from Fermentation Manager v1 (kinetics.py).

- Modified Gompertz and logistic (Zwietering et al., 1990) and Baranyi-Roberts (1994)
  models, fitted on y = ln(X/X0).
  Works on any growth-like variable (OD600, AFU, ...), not only biomass.
- Yields, productivity and specific rates with the integral method; the integral of X
  uses the MEASURED points (trapezoidal rule), not the fitted curve.

Changes from v1: English names; time at mu_max (inflection point) and doubling time
added; fit residual RMSE added; the buffer KPI moved to base_dosing.py because v2 reads
a cumulative volume from the controller instead of per-interval volumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import curve_fit

_trapz = getattr(np, "trapezoid", None) or np.trapz


def gompertz_modified(t: np.ndarray, A: float, mu_max: float, lam: float) -> np.ndarray:
    """Zwietering et al. (1990): y(t) = A exp(-exp(mu e / A (lam - t) + 1)), y = ln(X/X0)."""
    with np.errstate(over="ignore"):
        return A * np.exp(-np.exp((mu_max * np.e / A) * (lam - t) + 1))


def logistic_modified(t: np.ndarray, A: float, mu_max: float, lam: float) -> np.ndarray:
    """Zwietering et al. (1990): y(t) = A / (1 + exp(4 mu / A (lam - t) + 2)), y = ln(X/X0)."""
    with np.errstate(over="ignore"):
        return A / (1 + np.exp((4 * mu_max / A) * (lam - t) + 2))


def baranyi(t: np.ndarray, A: float, mu_max: float, lam: float) -> np.ndarray:
    """
    Baranyi & Roberts (1994), with y0 = 0 and y_max = A (y = ln(X/X0)) and h0 = mu_max * lam:
    y = mu B(t) - ln(1 + (exp(mu B(t)) - 1) / exp(A)),
    B(t) = t + ln(exp(-mu t) + exp(-h0) - exp(-mu t - h0)) / mu.
    """
    t = np.asarray(t, dtype=float)
    h0 = mu_max * lam
    # ln(e^-mu t + e^-h0 - e^-(mu t + h0)) = ln(e^-mu t (1 - e^-h0) + e^-h0), computed in log space
    mt = mu_max * t
    with np.errstate(divide="ignore"):
        log_q = np.logaddexp(-mt + np.log(-np.expm1(-h0)) if h0 > 0 else -np.inf, -h0)
    mb = mt + log_q
    # ln(1 + (e^mb - 1) e^-A) = ln((1 - e^-A) + e^(mb - A))
    return mb - np.logaddexp(np.log(-np.expm1(-A)), mb - A)


MODELS = {
    "gompertz": ("Modified Gompertz (Zwietering)", gompertz_modified),
    "logistic": ("Modified logistic (Zwietering)", logistic_modified),
    "baranyi": ("Baranyi-Roberts", baranyi),
}


@dataclass
class GrowthFit:
    model: str
    A: float
    mu_max: float
    lam: float
    A_se: float
    mu_max_se: float
    lam_se: float
    r_squared: float
    rmse: float
    X0: float
    n_points: int
    t_fit: np.ndarray
    X_fit: np.ndarray
    n_times: int = 0

    @property
    def warnings(self) -> list[str]:
        """Reasons not to trust the parameters (shown next to them, never hidden)."""
        w = []
        if self.n_times < 6:
            w.append(f"only {self.n_times} distinct time points for 3 parameters")
        bad = [n for n, v, se in (("λ", self.lam, self.lam_se), ("μmax", self.mu_max, self.mu_max_se), ("A", self.A, self.A_se))
               if not np.isfinite(se) or (v > 0 and se / v > 0.5)]
        if bad:
            w.append(f"{', '.join(bad)} poorly determined (standard error > 50 % of the value)")
        return w

    @property
    def t_mu_max(self) -> float:
        """Time of maximum growth rate (inflection point of y = ln(X/X0))."""
        if self.model == "gompertz":
            return self.lam + self.A / (self.mu_max * np.e)
        if self.model == "logistic":
            return self.lam + self.A / (2 * self.mu_max)
        t = np.linspace(0, max(self.t_fit.max(), self.lam * 2 + 1), 4000)
        y = MODELS[self.model][1](t, self.A, self.mu_max, self.lam)
        return float(t[int(np.argmax(np.gradient(y, t)))])

    @property
    def doubling_time(self) -> float:
        return float(np.log(2) / self.mu_max) if self.mu_max > 0 else float("nan")

    def summary(self) -> dict:
        return {
            "model": self.model,
            "lag_h": self.lam, "lag_se": self.lam_se,
            "mu_max_per_h": self.mu_max, "mu_max_se": self.mu_max_se,
            "t_mu_max_h": self.t_mu_max,
            "A_ln": self.A, "A_se": self.A_se,
            "X0": self.X0, "X_max": self.X0 * float(np.exp(self.A)),
            "doubling_time_h": self.doubling_time,
            "r_squared": self.r_squared, "rmse": self.rmse, "n_points": self.n_points,
            "n_times": self.n_times, "warnings": self.warnings,
        }

    def curve(self) -> dict:
        return {"t": self.t_fit.tolist(), "y": self.X_fit.tolist()}


GompertzFit = GrowthFit  # backwards-compatible name


def fit_growth(t, X, model: str = "gompertz") -> GrowthFit:
    """
    Fit a primary growth model on y = ln(X/X0), X0 = first valid point. Statistics (R², RMSE)
    are on the ln scale. Raises ValueError with an explanation if data are insufficient or
    the fit fails.
    """
    if model not in MODELS:
        raise ValueError(f"Unknown model '{model}' (use one of {', '.join(MODELS)}).")
    f = MODELS[model][1]
    t = np.asarray(t, dtype=float)
    X = np.asarray(X, dtype=float)
    mask = np.isfinite(t) & np.isfinite(X) & (X > 0)
    t_v, X_v = t[mask], X[mask]
    order = np.argsort(t_v)
    t_v, X_v = t_v[order], X_v[order]
    if len(t_v) < 4:
        raise ValueError(f"Not enough valid points for a fit ({len(t_v)}, at least 4 needed).")
    X0 = X_v[0]
    y = np.log(X_v / X0)
    if y.max() <= 0:
        raise ValueError("No increase over the first point: nothing to fit.")
    # Starting values from the mean per time point (pooled replicates repeat time points)
    tu, inv = np.unique(np.round(t_v * 4) / 4, return_inverse=True)  # 15-min resolution for the guesses
    yu = np.bincount(inv, weights=y) / np.bincount(inv)
    A0 = max(yu.max(), 0.1)
    if len(tu) > 1:
        slopes = np.gradient(yu, tu)
        mu0 = max(float(slopes.max()), 0.01)
        i = int(np.argmax(slopes))
        lam0 = float(np.clip(tu[i] - yu[i] / mu0, 0.0, t_v.max()))
    else:
        mu0, lam0 = 0.1, 0.0
    # Several starting points (lag around the guess): keep the best least-squares solution
    starts = [(A0, mu0, lam0)] + [(A0, mu0 * k, l) for k in (0.5, 1.0) for l in
                                  np.linspace(0, float(np.median(tu)), 4)]
    best, err = None, None
    for p0 in starts:
        try:
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                popt_, pcov_ = curve_fit(f, t_v, y, p0=list(p0), bounds=([1e-6, 1e-6, 0], [np.inf, np.inf, t_v.max()]),
                                         maxfev=20000)
                sse = float(np.sum((y - f(t_v, *popt_)) ** 2))
        except (RuntimeError, ValueError) as e:
            err = e
            continue
        if np.isfinite(sse) and (best is None or sse < best[0] - 1e-12):
            best = (sse, popt_, pcov_)
    if best is None:
        raise ValueError(f"{MODELS[model][0]} fit did not converge: {err}")
    _, popt, pcov = best
    perr = np.sqrt(np.clip(np.diag(pcov), 0, None))
    y_pred = f(t_v, *popt)
    if not np.all(np.isfinite(y_pred)):
        raise ValueError(f"{MODELS[model][0]} fit produced non-finite values.")
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    t_dense = np.linspace(t_v.min(), t_v.max(), 200)
    return GrowthFit(
        model=model, A=float(popt[0]), mu_max=float(popt[1]), lam=float(popt[2]),
        A_se=float(perr[0]), mu_max_se=float(perr[1]), lam_se=float(perr[2]),
        r_squared=1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        rmse=float(np.sqrt(ss_res / len(y))), X0=float(X0), n_points=int(len(t_v)),
        t_fit=t_dense, X_fit=X0 * np.exp(f(t_dense, *popt)), n_times=int(len(np.unique(np.round(t_v, 2)))),
    )


def fit_gompertz(t, X) -> GrowthFit:
    return fit_growth(t, X, "gompertz")


@dataclass
class IntegralKPIs:
    delta_t: float
    delta_X: float
    integral_X: float
    delta_P: Optional[float] = None
    delta_S: Optional[float] = None
    Ypx: Optional[float] = None
    Yxs: Optional[float] = None
    volumetric_productivity: Optional[float] = None
    qp: Optional[float] = None
    qs: Optional[float] = None
    delta_pH: Optional[float] = None


def _ratio(a: float, b: float) -> float:
    return a / b if b != 0 else float("nan")


def compute_kpis(t, X, P=None, S=None, pH=None) -> IntegralKPIs:
    """Integral-method KPIs. Optional series left as None produce None (nothing invented)."""
    t = np.asarray(t, dtype=float)
    X = np.asarray(X, dtype=float)
    o = np.argsort(t)
    t, X = t[o], X[o]
    k = IntegralKPIs(delta_t=float(t[-1] - t[0]), delta_X=float(X[-1] - X[0]), integral_X=float(_trapz(X, t)))
    if P is not None:
        P = np.asarray(P, dtype=float)[o]
        k.delta_P = float(P[-1] - P[0])
        k.Ypx = _ratio(k.delta_P, k.delta_X)
        k.volumetric_productivity = _ratio(k.delta_P, k.delta_t)
        k.qp = _ratio(k.delta_P, k.integral_X)
    if S is not None:
        S = np.asarray(S, dtype=float)[o]
        k.delta_S = float(S[0] - S[-1])  # consumed
        k.Yxs = _ratio(k.delta_X, k.delta_S)
        k.qs = _ratio(k.delta_S, k.integral_X)
    if pH is not None:
        pH = np.asarray(pH, dtype=float)[o]
        k.delta_pH = float(pH[-1] - pH[0])
    return k
