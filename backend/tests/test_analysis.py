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


@pytest.mark.parametrize("model", ["gompertz", "logistic", "baranyi"])
def test_growth_models_recover_their_own_parameters(model):
    f = kinetics.MODELS[model][1]
    t = np.linspace(0, 18, 19)
    X = 0.08 * np.exp(f(t, 4.0, 0.9, 3.0))
    fit = kinetics.fit_growth(t, X, model)
    # y = ln(X/X0) with X0 = first point forces y(0) = 0, while the logistic model is ~0.04 at t = 0:
    # a small, known bias of the Zwietering parameterisation.
    tol = 0.03 if model == "logistic" else 1e-3
    assert fit.A == pytest.approx(4.0, rel=tol) and fit.mu_max == pytest.approx(0.9, rel=tol)
    assert fit.lam == pytest.approx(3.0, abs=0.1 if model == "logistic" else 1e-2) and fit.r_squared > 0.999
    assert fit.lam < fit.t_mu_max < 18 and fit.summary()["X_max"] == pytest.approx(0.08 * np.exp(4.0), rel=tol)


def test_grouped_points_and_binned_series():
    from fm2.analysis import report
    import pandas as pd
    g = report.grouped_points([0, 0, 2], [1.0, 3.0, 5.0])
    assert g["mean"] == [2.0, 5.0] and g["sd"][0] == pytest.approx(np.sqrt(2)) and g["sd"][1] is None
    t = np.arange(0, 4, 0.01)
    df = pd.DataFrame({"t_h": t, "ph": 6 + 0 * t, "base_cumulative_ml": np.floor(t * 60 * 0.05 / 0.4) * 0.4})
    s = report.binned_series(df, 1.0, ["ph", "base_rate", "base_cumulative_ml"])
    assert s["t"] == [0.5, 1.5, 2.5, 3.5] and s["ph"] == [6.0] * 4
    assert s["base_rate"][1] == pytest.approx(0.05, abs=0.01)


def test_fit_on_pooled_replicates_with_jittered_times():
    """Pooled replicates sampled seconds apart used to give inf slopes in the starting guess and a step fit."""
    rng = np.random.default_rng(3)
    t = np.concatenate([np.arange(0, 19, 2.0) + d for d in (0.0, 0.01, 0.02)])
    y = np.exp(kinetics.gompertz_modified(t, 4.0, 0.7, 2.5)) * (1 + rng.normal(0, 0.03, len(t)))
    fit = kinetics.fit_growth(t, y)
    assert fit.mu_max == pytest.approx(0.7, rel=0.1) and fit.lam == pytest.approx(2.5, abs=0.4) and fit.r_squared > 0.98


def test_stitch_counter_continues_across_resets_and_ignores_glitches():
    from fm2.analysis.base_dosing import CounterStitcher, count_resets, stitch_counter
    raw = [0.0, 0.5, 1.61, 1.60, float("nan"), 0.0, 0.3, 0.9, 0.0, 0.2]
    out = stitch_counter(raw)
    assert count_resets(raw) == 2
    assert np.isnan(out[4])
    # 1.60 is a glitch (drop < 0.05): kept as read; reset at index 5 adds 1.60, at index 8 adds 0.9
    assert list(np.round(out[[0, 2, 3, 5, 6, 7, 8, 9]], 2)) == [0.0, 1.61, 1.60, 1.60, 1.90, 2.50, 2.50, 2.70]
    st = CounterStitcher()
    live = [st.update(v) for v in (1.0, 1.5, 0.0, 0.4)]
    assert [round(v, 2) for v, _ in live] == [1.0, 1.5, 1.5, 1.9] and [r for _, r in live] == [False, False, True, False]


def test_summary_total_survives_counter_reset():
    from fm2.analysis.base_dosing import summarize
    t = np.arange(0, 4.01, 0.1)
    cum = t * 1.0            # 1 mL/h
    cum[t > 2.0] -= 2.0      # counter reset to 0 just after 2 h
    s = summarize(t, cum)
    assert s.total_ml == pytest.approx(3.9, abs=0.11)
