import pytest

from fm2.config import Settings
from fm2.drivers.base import PhControl
from fm2.drivers.simulator import FakeClock, SimulatedController
from fm2.services import commands

RANGES = Settings().setpoint_ranges


def test_confirmed_with_readback_and_limit_warning():
    sim = SimulatedController("R1", clock=FakeClock(), seed=0)
    res = commands.write_setpoint(sim, "temperature", 25.0, RANGES)
    assert res.status == "confirmed" and res.readback_value == 25.0 and res.old_value == 30.0
    assert res.warnings and "below the low process limit" in res.warnings[0]


def test_rejects_out_of_range_and_big_step():
    sim = SimulatedController("R1", clock=FakeClock(), seed=0)
    with pytest.raises(commands.CommandRejected):
        commands.write_setpoint(sim, "temperature", 80.0, RANGES)
    with pytest.raises(commands.CommandRejected):
        commands.write_setpoint(sim, "ph", 4.5, RANGES)  # step 1.5 > max 1.0
    assert sim.sp["temperature"] == 30.0 and sim.sp["ph"] == 6.0


def test_ph_control_only_from_planning():
    sim = SimulatedController("R1", clock=FakeClock(), seed=0)
    with pytest.raises(commands.CommandRejected):
        commands.load_ph_control(sim, PhControl("adaptive"), "fermenting")
    commands.load_ph_control(sim, PhControl("adaptive"), "planned")
    assert sim.read().ph_control.mode == "adaptive"
