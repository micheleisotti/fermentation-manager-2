"""
Loop configuration as shown in planning: what the controller has, in a compact form, with
plain-language warnings. Pure functions, no I/O (the driver reads, see read_loop_config).

The assignments themselves (which actuator acts on which loop) stay on the controller:
FM2 only reads them. A profile can record the assignments it was made for
(`loop_assignments`); planning compares them with the controller and does not load a loop's
control type and parameters when they differ.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from .drivers.base import CONTROL_LOOPS, LoopConfig, LoopInfo

LOOP_LABELS = {"ph": "pH", "temperature": "Temperature", "stirrer": "Stirrer", "do2": "dO2",
               "level": "Level / antifoam", "redox": "Redox"}
CONTINUOUS = ("ph", "temperature", "do2", "redox")


def _side(a) -> str:
    return "below SP" if a.side > 0 else "above SP" if a.side < 0 else "—"


def loop_warnings(loop: LoopInfo, config: LoopConfig) -> list[str]:
    w: list[str] = []
    if loop.available < 3:
        return ["not installed on the controller (Service level)"]
    if loop.available == 3:
        w.append("installed, but not selected as a control loop on the controller")
    if loop.key == "redox":
        return w
    if loop.key == "level":
        if loop.available == 4 and not loop.actuators:
            w.append("no pump assigned: the controller only reports the contact")
    elif loop.available == 4 and not loop.actuators and loop.has_actuator is False:
        w.append("no actuator assigned: the loop cannot act")
    if loop.control_type == "level" and loop.key in CONTINUOUS:
        w.append("control type Level (on/off) on a continuous probe")
    if loop.control_type == "external" and loop.key in CONTINUOUS:
        w.append("control type External: the controller does not regulate it")
    for a in loop.actuators:
        if a.pump and a.calibrated is False:
            w.append(f"{a.name}: not calibrated in mL/min (its volume is not recorded)")
        if a.pump and a.dose_monitoring is False:
            w.append(f"{a.name}: dose monitoring off on the controller")
    if loop.key == "do2":
        try:
            val = float(loop.value) if loop.value is not None else None
        except ValueError:
            val = None
        if val is not None and val <= 1.0:
            w.append(f"probe reads {val:g} %: not polarised or not calibrated?")
        if loop.setpoint is not None and loop.setpoint > 100:
            w.append(f"setpoint {loop.setpoint:g} % (above 100 %)")
    for other in config.loops:
        if other.drives == loop.key:
            w.append(f"starting it also starts the {LOOP_LABELS.get(other.key, other.key)} loop (cascade)")
    return w


def config_view(config: LoopConfig) -> dict:
    """JSON for the planning box: one entry per loop plus pumps in no loop."""
    out = []
    for lp in config.loops:
        d = asdict(lp)
        d["label"] = LOOP_LABELS.get(lp.key, lp.key)
        d["installed"] = lp.available >= 3
        d["actuators"] = [asdict(a) | {"side_label": _side(a)} for a in lp.actuators]
        d["warnings"] = loop_warnings(lp, config)
        out.append(d)
    return {"loops": out, "unassigned_pumps": [asdict(a) for a in config.unassigned_pumps]}


def assignments_of(config: LoopConfig, loops=CONTROL_LOOPS) -> dict:
    """What a profile records: per loop, the actuators (and cascade loops) with their side."""
    return {lp.key: [{"id": a.id, "name": a.name, "side": a.side, "kind": a.kind} for a in lp.actuators]
            for lp in config.loops if lp.key in loops}


def _norm(items) -> list[tuple]:
    return sorted((it.get("kind", "actuator"), int(it["id"]), int(it["side"])) for it in (items or []))


def assignment_differences(expected: Optional[dict], config: LoopConfig) -> dict[str, str]:
    """{loop: message} for loops whose controller assignments differ from what the profile was made for."""
    if not expected:
        return {}
    now = assignments_of(config, tuple(expected))
    out = {}
    for loop, items in expected.items():
        if _norm(items) != _norm(now.get(loop)):
            def fmt(xs):
                return ", ".join(f"{x['name']} ({'+' if x['side'] > 0 else '−' if x['side'] < 0 else '0'})"
                                 for x in xs) or "nothing"
            out[loop] = (f"profile made for {fmt(items)}; controller has {fmt(now.get(loop, []))}")
    return out
