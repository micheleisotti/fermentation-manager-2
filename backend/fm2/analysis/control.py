"""
How well the controller held each process value at its setpoint (decision 12 Nov 2026: descriptive
metrics first; rule-based suggestions for the loop parameters come later, in 2.2).

Regulation phase. A loop "starts regulating" the first time the value reaches its setpoint: the error
(value − setpoint) changes sign, or comes within the tolerance. Typical cases: pH that starts above the
setpoint and drops with acidification; temperature that starts below and heats up. Before that the
value is approaching the target and says nothing about the loop. When the loop on/off state was
recorded (loop_<key> columns, from 2.1.3 on), only readings with the loop on count; earlier runs are
assumed on from the start of regulation to the end.

All statistics are time-weighted (each reading counts for the time to the next one, capped at
MAX_STEP_S so that gaps in the data do not weigh), and use the setpoint in force at each reading,
so setpoint changes during the run are handled.

Dose episodes (pH loop). From the cumulative dose counter: an episode is a run of readings in which
the counter grows, merged when the pauses between doses are shorter than EPISODE_GAP_S. For each
episode: pH when dosing started (last reading before the first dose), pH when it stopped (reading
of the last dose), highest pH in the EPISODE_PEAK_S after it, volume. With slow sampling the gap
is at least 2.5 sampling intervals. Resolution is the sampling
interval (5 s on the lab PC).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

# Tolerance around the setpoint for "time within tolerance" and for the start of regulation.
TOLERANCE = {"ph": 0.05, "temperature": 0.3, "stirrer": 5.0, "do2": 5.0}
UNITS = {"ph": "", "temperature": "°C", "stirrer": "rpm", "do2": "%"}
LABELS = {"ph": "pH", "temperature": "Temperature", "stirrer": "Stirrer", "do2": "dO2"}
# value column, setpoint column, recorded loop state column
COLUMNS = {"ph": ("ph", "ph_setpoint", "loop_ph"), "temperature": ("temperature", "temperature_setpoint", "loop_temperature"),
           "stirrer": ("stirrer", "stirrer_setpoint", "loop_stirrer"), "do2": ("do2", "do2_setpoint", "loop_do2")}

MAX_STEP_S = 120.0       # a reading never weighs more than 2 min
EPISODE_GAP_S = 60.0     # doses closer than this belong to the same episode
EPISODE_PEAK_S = 300.0   # look for the pH peak in the 5 min after an episode
DOSE_STEP_ML = 0.005     # counter has 2 decimals: an increase of 0.01 mL is one step


@dataclass
class LoopPerformance:
    loop: str
    label: str
    unit: str
    tolerance: float
    regulated: bool                       # False: the value never reached the setpoint
    t_reached_h: Optional[float] = None   # time from Start to the first time at the setpoint
    regulated_h: Optional[float] = None   # time counted in the statistics
    loop_state_recorded: bool = False     # True: only readings with the loop on are counted
    setpoint_mean: Optional[float] = None
    setpoint_changes: int = 0
    mean: Optional[float] = None          # effective mean during regulation
    sd: Optional[float] = None
    bias: Optional[float] = None          # mean(value − setpoint): systematic offset
    mae: Optional[float] = None           # mean |value − setpoint|
    rmse: Optional[float] = None
    max_above: Optional[float] = None     # largest excursion above the setpoint
    max_below: Optional[float] = None     # largest excursion below (as a positive number)
    within_tol_pct: Optional[float] = None
    crossings_per_h: Optional[float] = None   # times per hour the value crosses the tolerance band
    overshoot: Optional[float] = None     # largest excursion beyond the setpoint in the first hour after reaching it


@dataclass
class DoseEpisodes:
    counter: str
    n: int = 0
    ph_start_median: Optional[float] = None    # pH when dosing started
    ph_start_range: Optional[list] = None
    ph_stop_median: Optional[float] = None     # pH at the last dose of the episode
    ph_peak_median: Optional[float] = None     # highest pH in the 5 min after the episode
    volume_median_ml: Optional[float] = None
    interval_median_min: Optional[float] = None
    setpoint_mean: Optional[float] = None
    start_below_setpoint_median: Optional[float] = None   # setpoint − pH at start (base) / pH − setpoint (acid)
    episodes: list = field(default_factory=list)


def _weights(t_h: np.ndarray) -> np.ndarray:
    """Seconds each reading stands for: time to the next reading, capped at MAX_STEP_S (or 3 sampling
    intervals, if sampling is slower) so that gaps in the data do not weigh."""
    if len(t_h) < 2:
        return np.ones_like(t_h)
    dt = np.diff(t_h) * 3600.0
    med = float(np.median(dt))
    dt = np.append(dt, med)
    return np.clip(dt, 0.0, max(MAX_STEP_S, 3.0 * med))


def regulation_start(err: np.ndarray, tol: float) -> Optional[int]:
    """Index of the first reading at the setpoint: |error| ≤ tol, or the error changed sign."""
    ok = np.where(np.isfinite(err))[0]
    if len(ok) == 0:
        return None
    first = err[ok[0]]
    for i in ok:
        if abs(err[i]) <= tol or np.sign(err[i]) != np.sign(first):
            return int(i)
    return None


def _crossings(err: np.ndarray, tol: float) -> int:
    """Times the value leaves the tolerance band on one side after having been on the other (hysteresis)."""
    side, n = 0, 0
    for e in err:
        s = 1 if e > tol else -1 if e < -tol else 0
        if s and side and s != side:
            n += 1
        if s:
            side = s
    return n


def loop_performance(loop: str, t_h, value, setpoint, active=None) -> LoopPerformance:
    tol = TOLERANCE[loop]
    t = np.asarray(t_h, dtype=float)
    v = np.asarray(value, dtype=float)
    sp = np.asarray(setpoint, dtype=float)
    out = LoopPerformance(loop, LABELS[loop], UNITS[loop], tol, regulated=False)
    good = np.isfinite(t) & np.isfinite(v) & np.isfinite(sp) & (t >= 0)    # from Start on
    if good.sum() < 3:
        return out
    t, v, sp = t[good], v[good], sp[good]
    act = None
    if active is not None:
        a = np.asarray(active, dtype=float)[good]
        if np.isfinite(a).any():
            act = a
    err = v - sp
    i0 = regulation_start(err, tol)
    if i0 is None:
        out.setpoint_mean = float(np.mean(sp))
        return out
    out.regulated = True
    out.t_reached_h = float(t[i0])
    w = _weights(t)
    sel = np.arange(len(t)) >= i0
    if act is not None:
        out.loop_state_recorded = True
        sel &= act == 1
    if sel.sum() < 2:
        return out
    ws, e, vv, ss = w[sel], err[sel], v[sel], sp[sel]
    W = ws.sum()
    if W <= 0:
        return out
    mean = float(np.sum(ws * vv) / W)
    out.regulated_h = float(W / 3600.0)
    out.setpoint_mean = float(np.sum(ws * ss) / W)
    out.setpoint_changes = int(np.sum(np.abs(np.diff(ss)) > 1e-9))
    out.mean = mean
    out.sd = float(np.sqrt(np.sum(ws * (vv - mean) ** 2) / W))
    out.bias = float(np.sum(ws * e) / W)
    out.mae = float(np.sum(ws * np.abs(e)) / W)
    out.rmse = float(np.sqrt(np.sum(ws * e ** 2) / W))
    out.max_above = float(max(0.0, np.max(e)))
    out.max_below = float(max(0.0, -np.min(e)))
    out.within_tol_pct = float(100.0 * np.sum(ws[np.abs(e) <= tol]) / W)
    out.crossings_per_h = float(_crossings(e, tol) / out.regulated_h) if out.regulated_h > 0 else None
    # overshoot: beyond the setpoint, on the side opposite to the approach, in the first hour
    approach = np.sign(err[np.where(np.isfinite(err))[0][0]])
    first_h = sel & (t <= t[i0] + 1.0)
    if approach and first_h.any():
        beyond = -approach * err[first_h]
        out.overshoot = float(max(0.0, np.max(beyond)))
    return out


def dose_episodes(t_h, cum_ml, ph, ph_sp, counter: str = "base") -> DoseEpisodes:
    """Dosing episodes of one pump on the pH loop (base: below the setpoint; acid: above)."""
    t = np.asarray(t_h, dtype=float)
    c = np.asarray(cum_ml, dtype=float)
    p = np.asarray(ph, dtype=float)
    s = np.asarray(ph_sp, dtype=float)
    out = DoseEpisodes(counter)
    ok = np.isfinite(t) & np.isfinite(c) & (t >= 0)
    idx = np.where(ok)[0]
    if len(idx) < 3:
        return out
    inc = np.diff(c[idx]) > DOSE_STEP_ML          # increase between idx[k] and idx[k+1]
    # pauses shorter than this join doses into one episode: 1 min, or 2.5 readings if sampling is slower
    gap_h = max(EPISODE_GAP_S, 2.5 * float(np.median(np.diff(t[idx]))) * 3600.0) / 3600.0
    eps: list[tuple[int, int]] = []               # (reading before first dose, reading of last dose)
    k = 0
    while k < len(inc):
        if not inc[k]:
            k += 1
            continue
        start, end = idx[k], idx[k + 1]
        j = k + 1
        while j < len(inc):
            if inc[j]:
                end, j = idx[j + 1], j + 1
                continue
            # pause: does another dose follow within the gap?
            nxt = next((m for m in range(j, len(inc)) if inc[m]), None)
            if nxt is not None and (t[idx[nxt]] - t[end]) <= gap_h:
                j = nxt
                continue
            break
        eps.append((start, end))
        k = j
    rows = []
    for a, b in eps:
        after = (t >= t[b]) & (t <= t[b] + EPISODE_PEAK_S / 3600.0) & np.isfinite(p)
        peak = (np.max(p[after]) if counter == "base" else np.min(p[after])) if after.any() else np.nan
        rows.append({"t_h": float(t[a]), "ph_start": float(p[a]) if np.isfinite(p[a]) else None,
                     "ph_stop": float(p[b]) if np.isfinite(p[b]) else None,
                     "ph_peak": float(peak) if np.isfinite(peak) else None,
                     "setpoint": float(s[a]) if np.isfinite(s[a]) else None,
                     "volume_ml": float(c[b] - c[a])})
    out.n = len(rows)
    out.episodes = rows
    if not rows:
        return out

    def med(key):
        x = [r[key] for r in rows if r[key] is not None]
        return float(np.median(x)) if x else None

    out.ph_start_median, out.ph_stop_median, out.ph_peak_median = med("ph_start"), med("ph_stop"), med("ph_peak")
    starts = [r["ph_start"] for r in rows if r["ph_start"] is not None]
    out.ph_start_range = [float(min(starts)), float(max(starts))] if starts else None
    out.volume_median_ml = med("volume_ml")
    out.setpoint_mean = med("setpoint")
    gaps = [r["setpoint"] - r["ph_start"] if counter == "base" else r["ph_start"] - r["setpoint"]
            for r in rows if r["setpoint"] is not None and r["ph_start"] is not None]
    out.start_below_setpoint_median = float(np.median(gaps)) if gaps else None
    if len(rows) > 1:
        out.interval_median_min = float(np.median(np.diff([r["t_h"] for r in rows])) * 60.0)
    return out


def summarize(df, channels: list[str], plan: Optional[dict] = None) -> dict:
    """{"loops": [LoopPerformance…], "doses": {"base": DoseEpisodes, "acid": …}} for the channels in use.
    With the experiment's plan (control mode and loop parameters loaded), each loop also gets "hints"."""
    loops = []
    for loop, (vcol, scol, acol) in COLUMNS.items():
        if loop not in channels or vcol not in df or scol not in df:
            continue
        perf = loop_performance(loop, df["t_h"], df[vcol].astype(float), df[scol].astype(float),
                                df[acol].astype(float) if acol in df else None)
        loops.append(asdict(perf))
    doses = {}
    if "ph" in channels and "ph" in df and "ph_setpoint" in df:
        for counter, col in (("base", "base_cumulative_ml"), ("acid", "acid_cumulative_ml")):
            if col in df and np.isfinite(df[col].astype(float)).any():
                d = dose_episodes(df["t_h"], df[col].astype(float), df["ph"].astype(float),
                                  df["ph_setpoint"].astype(float), counter)
                if d.n or counter == "base":
                    doses[counter] = asdict(d)
    plan = plan or {}
    for lp in loops:
        mode = plan.get(f"{lp['loop']}_control_mode")
        lp["control_mode"] = mode
        lp["hints"] = hints(lp, doses if lp["loop"] == "ph" else None, mode, plan.get(f"{lp['loop']}_pid"))
    return {"loops": loops, "doses": doses}


# --- rule-based hints (decision 12 Nov 2026, "level 2"): indications to evaluate, never values to apply ---
OSCILLATION_PER_H = 4.0     # more crossings of the tolerance band per hour than this = oscillating


def hints(perf: dict, doses: Optional[dict], mode: Optional[str], pid: Optional[dict]) -> list[dict]:
    """
    Plain-language hints from the regulation metrics of one loop. Each hint: {"kind": "ok"|"hint"|"note",
    "text"}. The rules are general control practice; how much to change is left to the user, and the ez2-Control
    conventions for I (integral time or gain) are not assumed. Adaptive: the controller tunes itself, so only
    the evaluation is given.
    """
    out: list[dict] = []
    if not perf.get("regulated") or perf.get("mean") is None:
        return out
    loop, tol = perf["loop"], perf["tolerance"]
    unit = f" {perf['unit']}" if perf["unit"] else ""
    bias, within = perf.get("bias") or 0.0, perf.get("within_tol_pct") or 0.0
    cross = perf.get("crossings_per_h") or 0.0
    if mode == "adaptive":
        out.append({"kind": "note", "text": "Adaptive control: the controller tunes P, I and D itself; "
                                            "only the evaluation applies (no parameter hints)."})
    if within >= 95 and abs(bias) <= tol / 2 and cross < OSCILLATION_PER_H:
        out.append({"kind": "ok", "text": f"Held within ±{tol:g}{unit} for {within:.0f} % of the time with no "
                                          "systematic offset: no change suggested."})
        return out
    if mode == "adaptive":
        return out
    p = pid or {}
    if cross >= OSCILLATION_PER_H:
        out.append({"kind": "hint", "text": f"Oscillates around the target ({cross:.1f} crossings of the ±{tol:g}{unit} band "
                                            "per hour): the loop reacts too strongly. Try a smaller P"
                                            + (" or a little more D" if loop == "temperature" else "") + "."})
    if abs(bias) > tol / 2 and cross < OSCILLATION_PER_H:
        side = "below" if bias < 0 else "above"
        out.append({"kind": "hint", "text": f"On average {abs(bias):.3g}{unit} {side} the target: a persistent offset. "
                                            "The integral action (I) is what removes it — make it act more strongly "
                                            "(check on the controller whether I is an integral time or a gain)."})
    ov = perf.get("overshoot")
    if loop == "temperature" and ov is not None and ov > 2 * tol:
        out.append({"kind": "hint", "text": f"Overshoot of {ov:.2g}{unit} after first reaching the target: "
                                            "try a smaller P or more D for the heat-up."})
    if loop == "ph" and doses:
        b = doses.get("base") or {}
        sp, start, peak = b.get("setpoint_mean"), b.get("start_below_setpoint_median"), b.get("ph_peak_median")
        db = p.get("deadband")
        if b.get("n", 0) >= 3 and start is not None and start > 2 * tol:
            if db is not None and start <= db + 0.03:
                out.append({"kind": "hint", "text": f"Base dosing starts {start:.2f} pH below the target, about at the "
                                                    f"dead band in the plan ({db:g}): to dose earlier and in smaller steps, reduce the "
                                                    "dead band."})
            else:
                out.append({"kind": "hint", "text": f"Base dosing starts {start:.2f} pH below the target"
                                                    + (f", beyond the dead band in the plan ({db:g})" if db is not None else "")
                                                    + ": small errors give too little output. A larger P, or a shorter "
                                                    "cycle time, makes the pump start earlier."})
        if b.get("n", 0) >= 3 and sp is not None and peak is not None and peak - sp > tol:
            out.append({"kind": "hint", "text": f"After each dosing episode the pH peaks {peak - sp:.2f} above the target: "
                                                "doses are too large or mixing is slow. Smaller P (or pulse time) gives "
                                                "smaller doses; stirring speed also affects the delay."})
    if not any(h["kind"] == "hint" for h in out):
        out.append({"kind": "note", "text": f"Within ±{tol:g}{unit} for {within:.0f} % of the time; no specific pattern "
                                            "found by the rules."})
    return out
