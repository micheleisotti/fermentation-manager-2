import numpy as np
import pandas as pd
import pytest

from fm2.analysis import control

DT_H = 5 / 3600


def _sawtooth(hours=10.0, sp=6.2, start=6.6, low=6.07, high=6.22, dose=0.3, drift=0.0015):
    """pH from 6.6 down to the setpoint, then: acid drift down to `low`, a base dose back to `high`."""
    t = np.arange(0, hours, DT_H)
    ph, cum, c, v = [], [], 0.0, start
    for x in t:
        if x < 2:
            v = start - 0.2 * x
        else:
            v -= drift
            if v < low:
                v, c = high, c + dose
        ph.append(v)
        cum.append(round(c, 2))
    return t, np.array(ph), np.array(cum), np.full(len(t), sp)


def test_regulation_start_and_effective_mean():
    t, ph, _, sp = _sawtooth()
    p = control.loop_performance("ph", t, ph, sp)
    assert p.regulated and p.t_reached_h == pytest.approx(1.75, abs=0.01)   # first time within ±0.05
    assert p.mean < 6.2 and p.bias == pytest.approx(p.mean - 6.2, abs=1e-6)  # runs low on average
    assert p.max_below == pytest.approx(0.13, abs=0.01) and p.max_above <= 0.05 + 1e-9
    assert 0 < p.within_tol_pct < 100


def test_never_reaches_setpoint():
    t = np.arange(0, 5, DT_H)
    p = control.loop_performance("temperature", t, np.full(len(t), 25.0), np.full(len(t), 30.0))
    assert not p.regulated and p.mean is None and p.setpoint_mean == 30.0


def test_only_readings_with_the_loop_on_count():
    t = np.arange(0, 4, DT_H)
    v = np.where(t < 2, 30.0, 33.0)                 # loop off in the second half: 3 °C away
    sp = np.full(len(t), 30.0)
    on = np.where(t < 2, 1.0, 0.0)
    p = control.loop_performance("temperature", t, v, sp, on)
    assert p.loop_state_recorded and p.mean == pytest.approx(30.0) and p.within_tol_pct == pytest.approx(100.0)
    q = control.loop_performance("temperature", t, v, sp)   # state unknown (older runs): all counted
    assert q.mean > 31


def test_setpoint_change_during_the_run():
    t = np.arange(0, 6, DT_H)
    sp = np.where(t < 3, 30.0, 37.0)
    v = sp.copy()
    p = control.loop_performance("temperature", t, v, sp)
    assert p.setpoint_changes == 1 and p.bias == pytest.approx(0.0) and p.within_tol_pct == pytest.approx(100.0)


def test_gaps_do_not_weigh():
    t = np.concatenate([np.arange(0, 1, DT_H), np.arange(5, 6, DT_H)])    # 4 h without data
    v = np.concatenate([np.full(len(np.arange(0, 1, DT_H)), 30.0), np.full(len(np.arange(5, 6, DT_H)), 31.0)])
    p = control.loop_performance("temperature", t, v, np.full(len(t), 30.0))
    assert p.mean == pytest.approx(30.5, abs=0.01) and p.regulated_h == pytest.approx(2.0, abs=0.05)


def test_dose_episodes_ph_at_pump_start_and_stop():
    t, ph, cum, sp = _sawtooth()
    d = control.dose_episodes(t, cum, ph, sp)
    assert d.n > 40
    assert d.ph_start_median == pytest.approx(6.07, abs=0.003)
    assert d.ph_peak_median == pytest.approx(6.22, abs=0.003)
    assert d.volume_median_ml == pytest.approx(0.3, abs=0.01)
    assert d.start_below_setpoint_median == pytest.approx(0.13, abs=0.005)
    assert d.interval_median_min == pytest.approx(0.15 / 0.0015 * 5 / 60, rel=0.05)


def test_doses_close_together_are_one_episode():
    t = np.arange(0, 1, DT_H)
    cum = np.zeros(len(t))
    for k, i in enumerate(range(100, 110, 3)):     # four pulses 15 s apart
        cum[i:] += 0.05
    cum[400:] += 0.05                                # a separate one, half an hour later
    d = control.dose_episodes(t, np.round(cum, 2), np.full(len(t), 6.1), np.full(len(t), 6.2))
    assert d.n == 2 and d.episodes[0]["volume_ml"] == pytest.approx(0.2)


def test_summarize_uses_the_channels():
    t, ph, cum, sp = _sawtooth(hours=4)
    df = pd.DataFrame({"t_h": t, "ph": ph, "ph_setpoint": sp, "base_cumulative_ml": cum,
                       "temperature": 30.0, "temperature_setpoint": 30.0, "stirrer": None, "stirrer_setpoint": 200.0})
    s = control.summarize(df, ["ph", "temperature"])
    assert [lp["loop"] for lp in s["loops"]] == ["ph", "temperature"]
    assert s["doses"]["base"]["n"] > 0 and "acid" not in s["doses"]
