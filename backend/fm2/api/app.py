"""
FastAPI application. One service process hosts the API, the acquisition thread and
(later) the built web interface, so a single Windows service covers everything.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..acquisition import Acquisition, _as_utc
from ..alerts import AlertParams
from ..analysis import base_dosing, kinetics, stats
from ..config import Settings
import logging

from ..db import add_missing_columns, make_engine, make_session_factory, session_scope
from ..drivers import ControllerDriver, DriverError, PhControl, make_driver
from ..models import (
    DEFAULT_VARIABLES, AnalysisSession, Base, Buffer, ControllerProfile, ControllerReading, Event,
    Experiment, ExperimentStatus, ManualMeasurement, ManualMeasurementEdit, Medium, ParallelRun,
    PhControlMode, SessionMember, SetpointCommand, Variable, VariableCategory,
)
from ..services import commands
from . import schemas as S


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_app(settings: Settings, drivers: Optional[dict[str, ControllerDriver]] = None,
               clock: Callable[[], datetime] = _utcnow, start_acquisition: bool = True,
               db_path=None) -> FastAPI:
    engine = make_engine(db_path or settings.db_path)
    Base.metadata.create_all(engine)
    for c in add_missing_columns(engine, Base):
        log.info("database: added column %s", c)
    sessions = make_session_factory(engine)
    with session_scope(sessions) as s:
        if not s.scalar(select(func.count()).select_from(Variable)):
            s.add_all(Variable(name=n, unit=u, category=c) for n, u, c in DEFAULT_VARIABLES)

    drivers = drivers or {r.reactor_id: make_driver(r.driver, r.reactor_id, r.address) for r in settings.reactors}
    acq = Acquisition(drivers, sessions, settings.sampling_interval_s, clock=clock, alert_params=AlertParams(
        out_of_limits_after_s=settings.alert_out_of_limits_after_s, rearm_s=settings.alert_out_of_limits_rearm_s,
        ph_tolerance=settings.alert_ph_tolerance, temp_tolerance=settings.alert_temp_tolerance,
        hold_s=settings.alert_setpoint_hold_s, silent_after_s=settings.alert_silent_after_s,
        finished_fraction=settings.alert_finished_fraction_of_peak, finished_hold_s=settings.alert_finished_hold_s))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_acquisition:
            acq.start()
        yield
        acq.stop()

    app = FastAPI(title="Fermentation Manager", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.sessions, app.state.acq, app.state.clock = settings, sessions, acq, clock
    app.include_router(router, prefix="/api")
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the built React app (frontend/dist) with a fallback to index.html for client-side routes."""
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(index)


# --- dependencies ---------------------------------------------------------------------

def get_db(request: Request):
    s: Session = request.app.state.sessions()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def now_of(request: Request) -> datetime:
    return request.app.state.clock()


log = logging.getLogger(__name__)

router = APIRouter()


def _404(obj, what="Not found"):
    if obj is None:
        raise HTTPException(404, what)
    return obj


def _next_code(db: Session, model, prefix: str) -> str:
    n = (db.scalar(select(func.max(model.id))) or 0) + 1
    return f"{prefix}-{n:03d}"


def _exp_out(e: Experiment) -> S.ExperimentOut:
    out = S.ExperimentOut.model_validate(e)
    out.status = e.status.value
    if e.parallel_run:
        out.parallel_run_code = e.parallel_run.code
        out.parallel_partner_codes = [x.code for x in e.parallel_run.experiments if x.id != e.id]
    return out


def _t_h(e: Experiment, at: datetime) -> float:
    if e.started_at is None:
        raise HTTPException(409, "Experiment not started: time is counted from Start")
    return (_as_utc(at) - _as_utc(e.started_at)).total_seconds() / 3600.0


# --- health ------------------------------------------------------------------------------

@router.get("/health")
def health(request: Request):
    return {"status": "ok", "version": __version__}


# --- catalogues -------------------------------------------------------------------------

def _catalogue(path: str, model):
    @router.get(f"/{path}", response_model=list[S.NamedOut], name=f"list_{path}")
    def _list(db: Session = Depends(get_db), include_archived: bool = False):
        q = select(model).order_by(model.created_at.desc())
        if not include_archived:
            q = q.where(model.archived.is_(False))
        return db.scalars(q).all()

    @router.post(f"/{path}", response_model=S.NamedOut, status_code=201, name=f"create_{path}")
    def _create(body: S.NamedIn, db: Session = Depends(get_db)):
        if db.scalar(select(model).where(model.name == body.name)):
            raise HTTPException(409, f"'{body.name}' already exists")
        obj = model(name=body.name, description=body.description)
        db.add(obj)
        db.flush()
        return obj

    @router.post(f"/{path}/{{item_id}}/archive", response_model=S.NamedOut, name=f"archive_{path}")
    def _archive(item_id: int, db: Session = Depends(get_db)):
        obj = _404(db.get(model, item_id))
        obj.archived = True  # used items are archived, never deleted
        return obj


_catalogue("media", Medium)
_catalogue("buffers", Buffer)


@router.get("/variables", response_model=list[S.VariableOut])
def list_variables(db: Session = Depends(get_db)):
    return [S.VariableOut(id=v.id, name=v.name, unit=v.unit, category=v.category.value)
            for v in db.scalars(select(Variable).order_by(Variable.name))]


@router.post("/variables", response_model=S.VariableOut, status_code=201)
def create_variable(body: S.VariableIn, db: Session = Depends(get_db)):
    try:
        cat = VariableCategory(body.category)
    except ValueError:
        raise HTTPException(422, f"Category must be one of {[c.value for c in VariableCategory]}")
    v = Variable(name=body.name, unit=body.unit, category=cat)
    db.add(v)
    db.flush()
    return S.VariableOut(id=v.id, name=v.name, unit=v.unit, category=v.category.value)


@router.get("/profiles", response_model=list[S.ProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_profile_out(p) for p in db.scalars(select(ControllerProfile).order_by(ControllerProfile.name))]


def _profile_out(p: ControllerProfile) -> S.ProfileOut:
    out = S.ProfileOut.model_validate(p)
    out.ph_control_mode = p.ph_control_mode.value if p.ph_control_mode else None
    return out


@router.post("/profiles", response_model=S.ProfileOut, status_code=201)
def create_profile(body: S.ProfileIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    if data["ph_control_mode"]:
        data["ph_control_mode"] = PhControlMode(data["ph_control_mode"])
    p = ControllerProfile(**data)
    db.add(p)
    db.flush()
    return _profile_out(p)


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """
    Delete a profile. Refused while a planned experiment uses it (its plan would be lost).
    Started/completed experiments keep their own copy (plan_snapshot) and just lose the link.
    """
    p = _404(db.get(ControllerProfile, profile_id), "Profile not found")
    users = db.scalars(select(Experiment).where(Experiment.profile_id == profile_id)).all()
    planned = [e.code for e in users if e.status == ExperimentStatus.planned]
    if planned:
        raise HTTPException(409, f"Profile '{p.name}' is used by planned experiment(s) {', '.join(planned)}: "
                                 "choose another profile for them first.")
    for e in users:
        e.profile_id = None
    db.delete(p)
    db.flush()
    return {"deleted": profile_id, "unlinked_experiments": [e.code for e in users]}


# --- experiments ------------------------------------------------------------------------

@router.get("/experiments", response_model=list[S.ExperimentOut])
def list_experiments(db: Session = Depends(get_db), status: Optional[str] = None, q: Optional[str] = None):
    stmt = select(Experiment).order_by(Experiment.created_at.desc())
    if status:
        stmt = stmt.where(Experiment.status == ExperimentStatus(status))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Experiment.name.ilike(like) | Experiment.strain.ilike(like) | Experiment.code.ilike(like))
    return [_exp_out(e) for e in db.scalars(stmt)]


@router.post("/experiments", response_model=S.ExperimentOut, status_code=201)
def create_experiment(body: S.ExperimentIn, request: Request, db: Session = Depends(get_db)):
    if body.reactor_id not in request.app.state.acq.drivers:
        raise HTTPException(422, f"Unknown reactor {body.reactor_id}")
    data = body.model_dump()
    data["planned_date"] = data["planned_date"] or now_of(request).date().isoformat()
    e = Experiment(code=_next_code(db, Experiment, "FM"), **data)
    db.add(e)
    db.flush()
    return _exp_out(e)


@router.get("/experiments/{exp_id}", response_model=S.ExperimentOut)
def get_experiment(exp_id: int, db: Session = Depends(get_db)):
    return _exp_out(_404(db.get(Experiment, exp_id)))


@router.patch("/experiments/{exp_id}", response_model=S.ExperimentOut)
def patch_experiment(exp_id: int, body: S.ExperimentPatch, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    data = body.model_dump(exclude_unset=True)
    plan_only = {"reactor_id", "profile_id"}
    if e.status != ExperimentStatus.planned and plan_only & data.keys():
        raise HTTPException(409, "Reactor and profile can only be changed while planned")
    for k, v in data.items():
        setattr(e, k, v)
    return _exp_out(e)


@router.post("/experiments/{exp_id}/parallel", response_model=S.ExperimentOut)
def link_parallel(exp_id: int, body: S.ParallelIn, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    p = _404(db.get(Experiment, body.partner_id), "Partner not found")
    if e.id == p.id or e.reactor_id == p.reactor_id:
        raise HTTPException(422, "A parallel run needs two experiments on different reactors")
    run = e.parallel_run or p.parallel_run
    if run is None:
        run = ParallelRun(code=_next_code(db, ParallelRun, "PR"))
        db.add(run)
        db.flush()
    e.parallel_run, p.parallel_run = run, run
    db.flush()
    return _exp_out(e)


@router.post("/experiments/{exp_id}/load-profile")
def load_profile(exp_id: int, request: Request, db: Session = Depends(get_db)):
    """Planning only: write the profile's setpoints and pH control (mode + PID) to the controller."""
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.planned:
        raise HTTPException(409, "Profiles can only be loaded while the experiment is planned")
    prof = _404(e.profile, "No profile selected")
    acq: Acquisition = request.app.state.acq
    driver = acq.drivers[e.reactor_id]
    ranges = request.app.state.settings.setpoint_ranges
    results = []
    for param, value in (("ph", prof.ph_setpoint), ("temperature", prof.temperature_setpoint),
                         ("stirrer", prof.stirrer_setpoint)):
        if value is None:
            continue
        try:
            cur = driver.read().setpoint(param)
            commands.validate_setpoint(param, value, None, ranges)
            driver.write_setpoint(param, value)
            rb = driver.read().setpoint(param)
            status = "confirmed" if rb is not None and abs(rb - value) < 1e-6 else "mismatch"
        except (DriverError, commands.CommandRejected) as ex:
            cur, rb, status = None, None, "error"
            results.append({"parameter": param, "status": status, "message": str(ex)})
            continue
        db.add(SetpointCommand(experiment_id=e.id, reactor_id=e.reactor_id, parameter=param, old_value=cur,
                               requested_value=value, readback_value=rb, status=status, origin="profile"))
        results.append({"parameter": param, "status": status, "readback": rb})
    for param, lo, hi in (("ph", prof.ph_low_limit, prof.ph_high_limit),
                          ("temperature", prof.temperature_low_limit, prof.temperature_high_limit)):
        if lo is None and hi is None:
            continue
        try:
            for r in commands.write_limits(driver, param, lo, hi):
                db.add(SetpointCommand(experiment_id=e.id, reactor_id=e.reactor_id, parameter=r.parameter,
                                       old_value=r.old_value, requested_value=r.requested_value,
                                       readback_value=r.readback_value, status=r.status, message=r.message,
                                       origin="profile"))
                results.append({"parameter": r.parameter, "status": r.status, "readback": r.readback_value,
                                **({"message": r.message} if r.status != "confirmed" else {})})
        except commands.CommandRejected as ex:
            results.append({"parameter": f"{param}_limits", "status": "error", "message": str(ex)})
    if prof.ph_control_mode:
        try:
            commands.load_ph_control(driver, PhControl(prof.ph_control_mode.value, prof.ph_pid), e.status.value)
            results.append({"parameter": "ph_control", "status": "confirmed"})
        except (DriverError, commands.CommandRejected) as ex:
            results.append({"parameter": "ph_control", "status": "error", "message": str(ex)})
    return {"results": results}


@router.get("/experiments/{exp_id}/plan-check")
def plan_check(exp_id: int, request: Request, db: Session = Depends(get_db)):
    """Compare the plan (profile) with what the controller reports now."""
    e = _404(db.get(Experiment, exp_id))
    acq: Acquisition = request.app.state.acq
    try:
        snap = acq.drivers[e.reactor_id].read()
    except DriverError as ex:
        return {"reachable": False, "message": str(ex), "rows": []}
    rows = []
    p = e.profile
    if p:
        for param, planned in (("ph", p.ph_setpoint), ("temperature", p.temperature_setpoint),
                               ("stirrer", p.stirrer_setpoint)):
            ctrl = snap.setpoint(param)
            rows.append({"parameter": f"{param}_setpoint", "plan": planned, "controller": ctrl,
                         "match": planned is None or (ctrl is not None and abs(ctrl - planned) < 1e-6)})
        if p.ph_control_mode:
            rows.append({"parameter": "ph_control_mode", "plan": p.ph_control_mode.value,
                         "controller": snap.ph_control.mode, "match": p.ph_control_mode.value == snap.ph_control.mode})
            plan_ctl = PhControl(p.ph_control_mode.value, p.ph_pid)
            param_diffs = [d for d in commands.ph_control_differences(plan_ctl, snap.ph_control)
                           if not d.startswith("mode ")]
            applicable = {k: v for k, v in (p.ph_pid or {}).items()
                          if k in commands.PH_MODE_PARAMS[p.ph_control_mode.value]}
            if applicable:
                rows.append({"parameter": "ph_pid", "plan": applicable,
                             "controller": {k: (snap.ph_control.pid or {}).get(k) for k in applicable},
                             "match": not param_diffs})
    for param in ("ph", "temperature"):
        ctrl = [getattr(snap, f"{param}_low_limit"), getattr(snap, f"{param}_high_limit")]
        plan = [getattr(p, f"{param}_low_limit"), getattr(p, f"{param}_high_limit")] if p else [None, None]
        if plan == [None, None]:     # profile leaves the limits as they are on the controller
            rows.append({"parameter": f"{param}_limits", "plan": None, "controller": ctrl, "match": True, "read_only": True})
        else:
            match = all(pv is None or (cv is not None and abs(cv - pv) < 1e-6) for pv, cv in zip(plan, ctrl))
            rows.append({"parameter": f"{param}_limits", "plan": plan, "controller": ctrl, "match": match})
    busy = db.scalar(select(Experiment).where(Experiment.reactor_id == e.reactor_id,
                                              Experiment.status == ExperimentStatus.fermenting,
                                              Experiment.id != e.id))
    return {"reachable": True, "reactor_free": busy is None, "rows": rows}


@router.post("/experiments/{exp_id}/start", response_model=S.ExperimentOut)
def start_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.planned:
        raise HTTPException(409, "Only planned experiments can be started")
    busy = db.scalar(select(Experiment).where(Experiment.reactor_id == e.reactor_id,
                                              Experiment.status == ExperimentStatus.fermenting))
    if busy:
        raise HTTPException(409, f"{e.reactor_id} is already recording {busy.code}")
    acq: Acquisition = request.app.state.acq
    driver = acq.drivers[e.reactor_id]
    if hasattr(driver, "on_experiment_start"):
        driver.on_experiment_start()  # simulator only: fresh culture
    e.status, e.started_at = ExperimentStatus.fermenting, now_of(request)
    if e.profile:
        p = e.profile
        e.plan_snapshot = {"profile": p.name, "ph_setpoint": p.ph_setpoint,
                           "temperature_setpoint": p.temperature_setpoint, "stirrer_setpoint": p.stirrer_setpoint,
                           "ph_control_mode": p.ph_control_mode.value if p.ph_control_mode else None,
                           "ph_pid": p.ph_pid,
                           "ph_limits": [p.ph_low_limit, p.ph_high_limit],
                           "temperature_limits": [p.temperature_low_limit, p.temperature_high_limit]}
    db.flush()
    db.commit()
    acq.poll_once()  # first reading at t = 0 (and immediate "at Start" alerts)
    db.refresh(e)
    return _exp_out(e)


def _close(exp_id: int, request: Request, db: Session, status: ExperimentStatus) -> S.ExperimentOut:
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.fermenting:
        raise HTTPException(409, "Experiment is not fermenting")
    e.status, e.ended_at = status, now_of(request)
    return _exp_out(e)


@router.post("/experiments/{exp_id}/end", response_model=S.ExperimentOut)
def end_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    return _close(exp_id, request, db, ExperimentStatus.completed)


@router.post("/experiments/{exp_id}/abort", response_model=S.ExperimentOut)
def abort_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    return _close(exp_id, request, db, ExperimentStatus.aborted)


@router.delete("/experiments/{exp_id}")
def delete_experiment(exp_id: int, request: Request, confirm: str = "", db: Session = Depends(get_db)):
    """
    Permanently delete an experiment that is not recording, with all its data (readings,
    manual measurements and their edit history, events, setpoint log, analysis-session
    membership). `confirm` must repeat the experiment code. A parallel run left with one
    experiment is dissolved.
    """
    e = _404(db.get(Experiment, exp_id))
    if e.status == ExperimentStatus.fermenting:
        raise HTTPException(409, f"{e.code} is recording: end it first.")
    if confirm.strip() != e.code:
        raise HTTPException(400, f"Type the experiment code ({e.code}) to confirm.")
    code, run = e.code, e.parallel_run
    db.execute(sa_delete(SetpointCommand).where(SetpointCommand.experiment_id == exp_id))
    db.execute(sa_delete(SessionMember).where(SessionMember.experiment_id == exp_id))
    e.parallel_run_id = None
    db.flush()
    if run is not None:
        rest = db.scalars(select(Experiment).where(Experiment.parallel_run_id == run.id)).all()
        if len(rest) <= 1:
            for x in rest:
                x.parallel_run_id = None
            db.flush()
            db.delete(run)
    db.delete(e)          # readings, measurements (+ edits) and events go by ON DELETE CASCADE
    db.flush()
    request.app.state.acq.alert_states.pop(exp_id, None)
    return {"deleted": code}


# --- data ---------------------------------------------------------------------------------

READING_FIELDS = ["t_h", "ph", "ph_setpoint", "temperature", "temperature_setpoint", "stirrer", "stirrer_setpoint",
                  "base_cumulative_ml", "base_pump_pct", "ph_low_limit", "ph_high_limit",
                  "temperature_low_limit", "temperature_high_limit"]


def _readings_df(db: Session, exp_id: int, since_h: float = 0.0) -> pd.DataFrame:
    rows = db.execute(select(ControllerReading).where(ControllerReading.experiment_id == exp_id,
                                                      ControllerReading.t_h >= since_h)
                      .order_by(ControllerReading.t_h)).scalars().all()
    df = pd.DataFrame([{f: getattr(r, f) for f in READING_FIELDS} | {"ts": r.ts} for r in rows])
    return df if not df.empty else pd.DataFrame(columns=READING_FIELDS + ["ts"])


@router.get("/experiments/{exp_id}/readings")
def get_readings(exp_id: int, db: Session = Depends(get_db), since_h: float = 0.0, max_points: int = 2000):
    _404(db.get(Experiment, exp_id))
    df = _readings_df(db, exp_id, since_h)
    if len(df) > max_points:  # decimate for the browser; full data stays in the database/exports
        df = df.iloc[np.linspace(0, len(df) - 1, max_points).astype(int)]
    df = df.drop(columns=["ts"]).replace({np.nan: None})
    return {"columns": list(df.columns), "rows": df.values.tolist()}


def _meas_out(m: ManualMeasurement) -> S.MeasurementOut:
    return S.MeasurementOut(id=m.id, experiment_id=m.experiment_id, variable_id=m.variable_id,
                            variable_name=m.variable.name, sampled_at=m.sampled_at, t_h=m.t_h, value=m.value,
                            unit=m.unit, note=m.note, author=m.author)


@router.get("/experiments/{exp_id}/measurements", response_model=list[S.MeasurementOut])
def list_measurements(exp_id: int, db: Session = Depends(get_db)):
    _404(db.get(Experiment, exp_id))
    q = select(ManualMeasurement).where(ManualMeasurement.experiment_id == exp_id).order_by(ManualMeasurement.t_h)
    return [_meas_out(m) for m in db.scalars(q)]


@router.post("/experiments/{exp_id}/measurements", response_model=S.MeasurementOut, status_code=201)
def add_measurement(exp_id: int, body: S.MeasurementIn, request: Request, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    var = _404(db.get(Variable, body.variable_id), "Unknown variable")
    at = body.sampled_at or now_of(request)
    m = ManualMeasurement(experiment_id=e.id, variable_id=var.id, sampled_at=at, t_h=_t_h(e, at),
                          value=body.value, unit=body.unit or var.unit, note=body.note, author=body.author)
    db.add(m)
    db.flush()
    db.refresh(m)
    return _meas_out(m)


@router.patch("/measurements/{mid}", response_model=S.MeasurementOut)
def edit_measurement(mid: int, body: S.MeasurementPatch, db: Session = Depends(get_db)):
    m = _404(db.get(ManualMeasurement, mid))
    old = {"value": m.value, "sampled_at": m.sampled_at.isoformat(), "unit": m.unit, "note": m.note}
    data = body.model_dump(exclude_unset=True, exclude={"author"})
    for k, v in data.items():
        setattr(m, k, v)
    if "sampled_at" in data:
        m.t_h = _t_h(db.get(Experiment, m.experiment_id), m.sampled_at)
    new = {"value": m.value, "sampled_at": _as_utc(m.sampled_at).isoformat(), "unit": m.unit, "note": m.note}
    db.add(ManualMeasurementEdit(measurement_id=m.id, old=old, new=new, author=body.author))
    return _meas_out(m)


# --- live, setpoints, events ------------------------------------------------------------

@router.get("/reactors/live")
def reactors_live(request: Request, db: Session = Depends(get_db)):
    acq: Acquisition = request.app.state.acq
    now = now_of(request)
    out = []
    for rid, live in acq.live.items():
        exp = db.scalar(select(Experiment).where(Experiment.reactor_id == rid,
                                                 Experiment.status == ExperimentStatus.fermenting))
        snap = live.snapshot
        out.append({
            "reactor_id": rid,
            "online": live.last_error is None and live.last_ok is not None,
            "last_reading_age_s": (now - live.last_ok).total_seconds() if live.last_ok else None,
            "error": live.last_error,
            "snapshot": None if snap is None else {k: v for k, v in snap.__dict__.items() if k not in ("ts", "ph_control")},
            "ph_control": None if snap is None else snap.ph_control.__dict__,
            "experiment": None if exp is None else {"id": exp.id, "code": exp.code, "strain": exp.strain,
                                                    "t_h": _t_h(exp, now),
                                                    "parallel_run": exp.parallel_run.code if exp.parallel_run else None},
        })
    return out


@router.post("/reactors/{reactor_id}/setpoints")
def change_setpoint(reactor_id: str, body: S.SetpointIn, request: Request, db: Session = Depends(get_db)):
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id,
                                             Experiment.status == ExperimentStatus.fermenting))
    alert_state = acq.alert_state_for(exp.id, reactor_id) if exp else None
    try:
        res = commands.write_setpoint(driver, body.parameter, body.value,
                                      request.app.state.settings.setpoint_ranges, alert_state)
    except commands.CommandRejected as ex:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id,
                               parameter=body.parameter, requested_value=body.value, status="rejected",
                               message=str(ex), origin=body.origin, author=body.author))
        db.commit()
        raise HTTPException(422, str(ex))
    db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=res.parameter,
                           old_value=res.old_value, requested_value=res.requested_value,
                           readback_value=res.readback_value, status=res.status, message=res.message,
                           origin=body.origin, author=body.author))
    return res.__dict__


@router.post("/reactors/{reactor_id}/limits")
def change_limits(reactor_id: str, body: S.LimitsIn, request: Request, db: Session = Depends(get_db)):
    """Change the process (alarm) limits of pH or temperature, with read-back; logged per limit."""
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id,
                                             Experiment.status == ExperimentStatus.fermenting))
    try:
        results = commands.write_limits(driver, body.parameter, body.low, body.high)
    except commands.CommandRejected as ex:
        raise HTTPException(422, str(ex))
    for r in results:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=r.parameter,
                               old_value=r.old_value, requested_value=r.requested_value,
                               readback_value=r.readback_value, status=r.status, message=r.message,
                               origin=body.origin, author=body.author))
    return [r.__dict__ for r in results]


@router.get("/experiments/{exp_id}/setpoint-log")
def setpoint_log(exp_id: int, db: Session = Depends(get_db)):
    q = select(SetpointCommand).where(SetpointCommand.experiment_id == exp_id).order_by(SetpointCommand.at)
    return [{"at": c.at, "parameter": c.parameter, "old": c.old_value, "new": c.requested_value,
             "readback": c.readback_value, "status": c.status, "origin": c.origin, "author": c.author}
            for c in db.scalars(q)]


@router.get("/experiments/{exp_id}/events", response_model=list[S.EventOut])
def experiment_events(exp_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(Event).where(Event.experiment_id == exp_id).order_by(Event.at.desc())).all()


@router.get("/events/recent", response_model=list[S.EventOut])
def recent_events(db: Session = Depends(get_db), limit: int = 50):
    return db.scalars(select(Event).order_by(Event.at.desc()).limit(limit)).all()


# --- analysis -----------------------------------------------------------------------------

def _manual_series(db: Session, exp_id: int, variable: str) -> tuple[np.ndarray, np.ndarray]:
    q = (select(ManualMeasurement.t_h, ManualMeasurement.value).join(Variable)
         .where(ManualMeasurement.experiment_id == exp_id, Variable.name == variable).order_by(ManualMeasurement.t_h))
    rows = db.execute(q).all()
    return np.array([r[0] for r in rows], float), np.array([r[1] for r in rows], float)


def experiment_parameters(db: Session, exp_id: int, fit_variable: str = "OD600") -> dict:
    out: dict = {"fit_variable": fit_variable, "fit": None, "fit_error": None}
    t, y = _manual_series(db, exp_id, fit_variable)
    try:
        out["fit"] = kinetics.fit_gompertz(t, y).summary() if len(t) else None
        if not len(t):
            out["fit_error"] = f"No {fit_variable} measurements"
    except ValueError as ex:
        out["fit_error"] = str(ex)
    df = _readings_df(db, exp_id)
    t_od, od = _manual_series(db, exp_id, "OD600")
    final_od = float(od[-1]) if len(od) else None
    if not df.empty:
        bd = base_dosing.summarize(df["t_h"].astype(float), df["base_cumulative_ml"].astype(float), final_od)
        out["base_dosing"] = bd.__dict__
    else:
        out["base_dosing"] = None
    return out


@router.get("/experiments/{exp_id}/analysis")
def experiment_analysis(exp_id: int, db: Session = Depends(get_db), fit_variable: str = "OD600", bin_h: float = 0.25):
    _404(db.get(Experiment, exp_id))
    res = experiment_parameters(db, exp_id, fit_variable)
    df = _readings_df(db, exp_id)
    res["rate_bins"] = [] if df.empty else [b.__dict__ for b in base_dosing.rate_bins(
        df["t_h"].astype(float), df["base_cumulative_ml"].astype(float), bin_h)]
    return res


@router.post("/analysis-sessions", status_code=201)
def create_session(body: S.SessionIn, db: Session = Depends(get_db)):
    sess = AnalysisSession(code=_next_code(db, AnalysisSession, "AS"), name=body.name,
                           fit_variable=body.fit_variable, author=body.author)
    for eid, cond in body.members.items():
        e = _404(db.get(Experiment, eid), f"Experiment {eid} not found")
        if e.status != ExperimentStatus.completed:
            raise HTTPException(422, f"{e.code} is not completed")
        sess.members.append(SessionMember(experiment_id=eid, condition=cond))
    db.add(sess)
    db.flush()
    return {"id": sess.id, "code": sess.code}


@router.get("/analysis-sessions/{sid}/results")
def session_results(sid: int, db: Session = Depends(get_db), step_h: float = 2.0):
    sess = _404(db.get(AnalysisSession, sid))
    by_cond: dict[str, list[int]] = {}
    for m in sess.members:
        by_cond.setdefault(m.condition, []).append(m.experiment_id)
    params = {eid: experiment_parameters(db, eid, sess.fit_variable) for ids in by_cond.values() for eid in ids}

    def pick(eid: int, key: str):
        p = params[eid]
        if key in ("lag_h", "mu_max_per_h", "t_mu_max_h", "A_ln"):
            return p["fit"][key] if p["fit"] else None
        return p["base_dosing"][key] if p["base_dosing"] else None

    keys = ["lag_h", "mu_max_per_h", "t_mu_max_h", "A_ln", "total_ml", "peak_rate_ml_min", "t_peak_h",
            "t_half_total_h", "last_hour_rate_ml_min", "base_per_final_od"]
    comparisons = {}
    for k in keys:
        cmp = stats.compare_conditions({c: [pick(e, k) for e in ids] for c, ids in by_cond.items()})
        comparisons[k] = {"test": cmp.test, "statistic": cmp.statistic, "p_value": cmp.p_value,
                          "difference": cmp.difference, "ci95": cmp.ci95, "posthoc": cmp.posthoc,
                          "warning": cmp.warning,
                          "conditions": {c: s.__dict__ for c, s in cmp.conditions.items()}}
    curves = {}
    for c, ids in by_cond.items():
        rc = stats.replicate_curve([_manual_series(db, e, sess.fit_variable) for e in ids], step_h=step_h)
        curves[c] = None if rc is None else {"t": rc.t.tolist(), "mean": rc.mean.tolist(),
                                             "sd": [None if np.isnan(x) else x for x in rc.sd.tolist()],
                                             "n": rc.n, "warning": rc.warning}
    # Parallel-run flags: informational only, never used in statistics.
    exps = {e.id: e for e in db.scalars(select(Experiment).where(Experiment.id.in_(params.keys())))}
    runs: dict[str, list[str]] = {}
    for e in exps.values():
        if e.parallel_run:
            runs.setdefault(e.parallel_run.code, []).append(e.code)
    cond_of = {m.experiment_id: m.condition for m in sess.members}
    flags = [{"parallel_run": r, "experiments": [{"code": code, "condition": cond_of[eid]}
                                                for eid, e2 in exps.items() if (code := e2.code) in codes],
              "same_condition": len({cond_of[eid] for eid, e2 in exps.items() if e2.code in codes}) == 1}
             for r, codes in runs.items() if len(codes) > 1]
    return {"code": sess.code, "conditions": {c: len(ids) for c, ids in by_cond.items()},
            "comparisons": comparisons, "curves": curves, "parallel_run_flags": flags}


# --- export -------------------------------------------------------------------------------

@router.get("/experiments/{exp_id}/export.xlsx")
def export_xlsx(exp_id: int, db: Session = Depends(get_db), resample_min: Optional[float] = None):
    e = _404(db.get(Experiment, exp_id))
    readings = _readings_df(db, exp_id)
    if resample_min and not readings.empty:
        readings = readings.set_index(pd.to_datetime(readings["ts"], utc=True)).drop(columns=["ts"]) \
            .resample(f"{resample_min}min").mean().dropna(how="all").reset_index()
    if "ts" in readings:
        readings["ts"] = pd.to_datetime(readings["ts"], utc=True).dt.tz_localize(None)
    meas = pd.DataFrame([m.model_dump() for m in list_measurements(exp_id, db)])
    if not meas.empty:
        meas["sampled_at"] = pd.to_datetime(meas["sampled_at"], utc=True).dt.tz_localize(None)
    params = experiment_parameters(db, exp_id)
    meta = pd.DataFrame([{"field": k, "value": v} for k, v in {
        "code": e.code, "name": e.name, "strain": e.strain, "reactor": e.reactor_id, "status": e.status.value,
        "started_at (UTC)": e.started_at, "ended_at (UTC)": e.ended_at,
        "parallel_run": e.parallel_run.code if e.parallel_run else None,
        "profile": (e.plan_snapshot or {}).get("profile"), "notes": e.notes}.items()])
    par_rows = [{"block": "fit", "parameter": k, "value": v} for k, v in (params["fit"] or {}).items()]
    par_rows += [{"block": "base dosing", "parameter": k, "value": v} for k, v in (params["base_dosing"] or {}).items()]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        meta.to_excel(xw, sheet_name="Metadata", index=False)
        readings.to_excel(xw, sheet_name="Controller data", index=False)
        meas.to_excel(xw, sheet_name="Manual variables", index=False)
        pd.DataFrame(par_rows).to_excel(xw, sheet_name="Parameters", index=False)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{e.code}.xlsx"'})
