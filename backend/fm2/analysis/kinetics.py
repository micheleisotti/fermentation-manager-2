"""
Growth kinetics and integral KPIs, ported from Fermentation Manager v1 (kinetics.py).

- Modified Gompertz model (Zwietering et al., 1990), fitted on y = ln(X/X0).
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
    """y(t) = A * exp(-exp(mu_max * e / A * (lam - t) + 1)), with y = ln(X/X0)."""
    return A * np.exp(-np.exp((mu_max * np.e / A) * (lam - t) + 1))


@dataclass
class GompertzFit:
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

    @property
    def t_mu_max(self) -> float:
        """Time of maximum growth rate = inflection point of the Gompertz curve."""
        return self.lam + self.A / (self.mu_max * np.e)

    @property
    def doubling_time(self) -> float:
        return float(np.log(2) / self.mu_max) if self.mu_max > 0 else float("nan")

    def summary(self) -> dict:
        return {
            "lag_h": self.lam, "lag_se": self.lam_se,
            "mu_max_per_h": self.mu_max, "mu_max_se": self.mu_max_se,
            "t_mu_max_h": self.t_mu_max,
            "A_ln": self.A, "A_se": self.A_se,
            "X0": self.X0, "doubling_time_h": self.doubling_time,
            "r_squared": self.r_squared, "rmse": self.rmse, "n_points": self.n_points,
        }


def fit_gompertz(t, X) -> GompertzFit:
    """Raises ValueError with an explanation if data are insufficient or the fit fails."""
    t = np.asarray(t, dtype=float)
    X = np.asarray(X, dtype=float)
    mask = np.isfinite(t) & np.isfinite(X) & (X > 0)
    t_v, X_v = t[mask], X[mask]
    order = np.argsort(t_v)
    t_v, X_v = t_v[order], X_v[order]
    if len(t_v) < 4:
        raise ValueError(f"Not enough valid points for a Gompertz fit ({len(t_v)}, at least 4 needed).")
    X0 = X_v[0]
    y = np.log(X_v / X0)
    A0 = max(y.max(), 0.1)
    mu0 = max((y.max() - y.min()) / (t_v.max() - t_v.min() + 1e-9), 0.01)
    lam0 = t_v[np.argmax(np.gradient(y, t_v))] if len(t_v) > 1 else 0.0
    try:
        popt, pcov = curve_fit(gompertz_modified, t_v, y, p0=[A0, mu0, max(lam0, 0.0)],
                               bounds=([1e-6, 1e-6, 0], [np.inf, np.inf, t_v.max()]), maxfev=20000)
    except RuntimeError as e:
        raise ValueError(f"Gompertz fit did not converge: {e}")
    perr = np.sqrt(np.diag(pcov))
    y_pred = gompertz_modified(t_v, *popt)
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    t_dense = np.linspace(t_v.min(), t_v.max(), 200)
    return GompertzFit(
        A=float(popt[0]), mu_max=float(popt[1]), lam=float(popt[2]),
        A_se=float(perr[0]), mu_max_se=float(perr[1]), lam_se=float(perr[2]),
        r_squared=1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
        rmse=float(np.sqrt(ss_res / len(y))), X0=float(X0), n_points=int(len(t_v)),
        t_fit=t_dense, X_fit=X0 * np.exp(gompertz_modified(t_dense, *popt)),
    )


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
