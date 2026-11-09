import numpy as np
import pytest

from fm2.analysis import base_dosing, kinetics, stats


def test_gompertz_recovers_parameters():
    t = np.arange(0, 18.1, 1.0)
    A, mu, lam = 4.0, 0.8, 3.0
    X = 0.08 * np.exp(kinetics.gompertz_modified(t, A, mu, lam))
    fit = kinetics.fit_gompertz(t, X)
    assert fit.mu_max == pytest.approx(mu, rel=1e-3)
    assert fit.lam == pytest.approx(lam, rel=1e-3)
    assert fit.t_mu_max == pytest.approx(lam + A / (mu * np.e), rel=1e-3)
    assert fit.r_squared > 0.999


def test_gompertz_needs_four_points():
    with pytest.raises(ValueError):
        kinetics.fit_gompertz([0, 1, 2], [0.1, 0.2, 0.4])


def test_kpis_integral_method_matches_v1():
    t = np.array([0, 2, 4, 6.0])
    X = np.array([0.1, 0.5, 1.5, 2.0])
    P = np.array([0, 1, 3, 4.0])
    k = kinetics.compute_kpis(t, X, P=P)
    assert k.integral_X == pytest.approx(np.trapezoid(X, t))
    assert k.Ypx == pytest.approx(4 / 1.9)
    assert k.qp == pytest.approx(4 / np.trapezoid(X, t))


def test_rate_bins_staircase_and_gap():
    t = np.arange(0, 2.0001, 1 / 120)          # 30 s readings over 2 h
    cum = np.floor(t * 10) * 0.4                # one 0.4 mL dose every 6 min
    bins = base_dosing.rate_bins(t, cum, bin_h=1.0)
    assert [round(b.rate_ml_min, 4) for b in bins[:2]] == [round(4.0 / 60, 4)] * 2
    # remove 40 min of readings in the second hour: that bin becomes incomplete, not a fake drop
    keep = ~((t > 1.1) & (t < 1.8))
    bins_gap = base_dosing.rate_bins(t[keep], cum[keep], bin_h=1.0)
    assert bins_gap[1].rate_ml_min is None and not bins_gap[1].complete


def test_base_summary():
    t = np.linspace(0, 10, 1201)
    cum = np.where(t < 2, 0, np.floor((t - 2) * 6) * 0.4)
    cum = np.minimum(cum, 12.0)
    s = base_dosing.summarize(t, cum, final_od=4.0)
    assert s.total_ml == pytest.approx(12.0)
    assert s.n_doses == 30
    assert s.base_per_final_od == pytest.approx(3.0)
    assert s.last_hour_rate_ml_min == 0.0


def test_welch_with_ci_and_low_power_warning():
    c = stats.compare_conditions({"A": [1.0, 1.2, 1.1], "B": [0.6, 0.7, 0.65]})
    assert c.test.startswith("Welch")
    assert c.difference == pytest.approx(1.1 - 0.65)
    lo, hi = c.ci95
    assert lo < c.difference < hi
    assert c.warning


def test_three_conditions_tukey():
    c = stats.compare_conditions({"A": [1, 1.1, 0.9], "B": [2, 2.1, 1.9], "C": [1, 1.05, 0.95]})
    assert "Tukey" in c.test and len(c.posthoc) == 3


def test_single_replicate_no_test():
    c = stats.compare_conditions({"A": [1.0], "B": [2.0, 2.2]})
    assert c.p_value is None and c.warning


def test_replicate_curve_fixed_grid():
    t = np.arange(0, 16.1, 0.5)
    rc = stats.replicate_curve([(t, t), (t, t + 1)], step_h=2.0)
    assert list(rc.t) == list(np.arange(0, 16.1, 2.0))
    assert rc.mean[0] == pytest.approx(0.5)
