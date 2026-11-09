from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fm2.alerts import AlertParams, ReactorAlertState
from fm2.drivers.base import ControllerSnapshot

T0 = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


def snap(ph=6.0, temp=30.0, ph_sp=6.0, t_sp=30.0, stir_sp=200.0, cum=0.0):
    return ControllerSnapshot(ts=T0, ph=ph, ph_setpoint=ph_sp, temperature=temp, temperature_setpoint=t_sp,
                              stirrer=stir_sp, stirrer_setpoint=stir_sp, base_cumulative_ml=cum,
                              ph_low_limit=5.8, ph_high_limit=6.3, temperature_low_limit=28, temperature_high_limit=32)


def kinds(events):
    return [e.kind for e in events]


def test_out_of_limits_at_start_is_immediate_and_once_per_excursion():
    st = ReactorAlertState("R1")
    ev = st.observe(snap(ph=7.0), T0)
    assert "out_of_limits_at_start" in kinds(ev)
    # still outside for an hour: no repeat
    for m in range(1, 60):
        assert "out_of_limits" not in kinds(st.observe(snap(ph=6.9), T0 + timedelta(minutes=m)))


def test_out_of_limits_during_run_needs_persistence_and_rearms():
    st = ReactorAlertState("R1")
    st.observe(snap(), T0)
    t = T0
    got = []
    for m in range(1, 6):  # 5 min above the T high limit
        t = T0 + timedelta(minutes=m)
        got += kinds(st.observe(snap(temp=32.5), t))
    assert got.count("out_of_limits") == 1
    # brief return inside (< rearm) then out again: no new alert
    st.observe(snap(), t + timedelta(minutes=1))
    got2 = []
    for m in range(2, 8):
        got2 += kinds(st.observe(snap(temp=32.5), t + timedelta(minutes=m)))
    assert "out_of_limits" not in got2
    # inside for > 10 min re-arms, next excursion alerts again
    t2 = t + timedelta(minutes=10)
    for m in range(0, 12):
        st.observe(snap(), t2 + timedelta(minutes=m))
    t3 = t2 + timedelta(minutes=12)
    got3 = []
    for m in range(0, 5):
        got3 += kinds(st.observe(snap(temp=32.5), t3 + timedelta(minutes=m)))
    assert got3.count("out_of_limits") == 1


def test_setpoint_reached_once_despite_dosing_peaks():
    st = ReactorAlertState("R1")
    st.observe(snap(ph=7.0, ph_sp=6.2), T0)
    events = []
    t = T0
    # pH falls from 7.0 to 6.2, then oscillates 6.2 -> 6.3 with each dose for hours
    for m in range(1, 400):
        t = T0 + timedelta(minutes=m)
        ph = max(6.2, 7.0 - 0.01 * m) if m < 80 else (6.29 if m % 7 == 0 else 6.21)
        events += kinds(st.observe(snap(ph=ph, ph_sp=6.2), t))
    assert events.count("setpoint_reached") == 1


def test_setpoint_reached_rearms_when_setpoint_changes_temperature_both_directions():
    st = ReactorAlertState("R1", AlertParams(hold_s=120))
    st.observe(snap(temp=37, t_sp=37), T0)  # already at setpoint
    ev = []
    temp = 37.0
    for m in range(1, 200):  # remote change to 15 °C, cooling
        temp += (15 - temp) * 0.05
        ev += kinds(st.observe(snap(temp=temp, t_sp=15), T0 + timedelta(minutes=m)))
    assert ev.count("setpoint_reached") == 1


def test_already_at_setpoint_at_start():
    st = ReactorAlertState("R1")
    ev = kinds(st.observe(snap(ph=6.01), T0))
    assert ev.count("already_at_setpoint") == 2  # pH and T both within tolerance


def test_silent_controller_and_restore():
    st = ReactorAlertState("R1")
    st.observe(snap(), T0)
    ev = []
    for m in range(1, 8):
        ev += kinds(st.observe(None, T0 + timedelta(minutes=m)))
    assert ev.count("controller_silent") == 1
    assert "controller_restored" in kinds(st.observe(snap(), T0 + timedelta(minutes=9)))


def test_external_change_flagged_but_not_software_change():
    st = ReactorAlertState("R1")
    st.observe(snap(), T0)
    st.expect_setpoint("temperature", 25.0)
    assert "external_setpoint_change" not in kinds(st.observe(snap(t_sp=25.0), T0 + timedelta(minutes=1)))
    assert "external_setpoint_change" in kinds(st.observe(snap(t_sp=25.0, stir_sp=150), T0 + timedelta(minutes=2)))


def test_probably_finished():
    st = ReactorAlertState("R1")
    cum, ev, t = 0.0, [], T0
    for m in range(0, 16 * 60, 1):
        t = T0 + timedelta(minutes=m)
        h = m / 60
        rate = 0.05 if 4 < h < 10 else 0.0  # mL/min
        cum += rate
        ev += kinds(st.observe(snap(cum=round(cum / 0.4) * 0.4), t))
    assert ev.count("probably_finished") == 1


def test_channels_switched_off_raise_no_alerts():
    from datetime import datetime, timedelta, timezone
    from fm2.alerts import ReactorAlertState
    from fm2.drivers.base import ControllerSnapshot
    t0 = datetime(2026, 11, 9, tzinfo=timezone.utc)
    snap = ControllerSnapshot(ts=t0, ph=7.5, ph_setpoint=6.0, ph_low_limit=5.8, ph_high_limit=6.3,
                              temperature=45.0, temperature_setpoint=30.0, temperature_low_limit=28.0,
                              temperature_high_limit=32.0)
    st = ReactorAlertState("R1", channels=frozenset({"stirrer"}))
    assert st.observe(snap, t0) == []                                  # pH and T both far outside: silent
    st.channels = frozenset({"stirrer", "temperature"})                 # T switched on during the run
    assert st.observe(snap, t0 + timedelta(minutes=5)) == []           # outside for < 3 min: not yet
    ev = st.observe(snap, t0 + timedelta(minutes=9))
    assert [e.kind for e in ev] == ["out_of_limits"] and "°C" in ev[0].message
