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
from sqlalchemy.orm import Session, object_session

from .. import __version__
from ..acquisition import Acquisition, _as_utc
from ..alerts import AlertParams
from ..analysis import base_dosing, kinetics, report, stats
from ..config import Settings
import logging
from pathlib import Path

from ..backup import BackupError, BackupManager
from ..notify import Notifier, NotifyError, send as notify_send
from ..services.app_settings import AppSettings, SettingsError
from ..db import add_missing_columns, make_engine, make_session_factory, session_scope
from ..drivers.base import CHANNELS, DEFAULT_CHANNELS, EXTRA_FIELDS
from ..drivers import ControllerDriver, DriverError, PhControl, make_driver
from ..models import (
    DEFAULT_VARIABLES, AnalysisSession, Base, Buffer, ControllerProfile, ControllerReading, Event,
    Experiment, ExperimentStatus, ManualMeasurement, ManualMeasurementEdit, Medium, ParallelRun,
    PhControlMode, SessionMember, SetpointCommand, Variable, VariableCategory,
)
from ..services import commands
from .. import users as users_mod
from ..models import GUEST_NAME, User
from . import schemas as S


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_app(settings: Settings, drivers: Optional[dict[str, ControllerDriver]] = None,
               clock: Callable[[], datetime] = _utcnow, start_acquisition: bool = True,
               db_path=None) -> FastAPI:
    db_file = Path(db_path or settings.db_path)
    engine = make_engine(db_file)
    Base.metadata.create_all(engine)
    for c in add_missing_columns(engine, Base):
        log.info("database: added column %s", c)
    sessions = make_session_factory(engine)
    with session_scope(sessions) as s:
        if not s.scalar(select(User.id).where(User.name == GUEST_NAME)):
            s.add(User(name=GUEST_NAME))
        if not s.scalar(select(func.count()).select_from(Variable)):
            s.add_all(Variable(name=n, unit=u, category=c) for n, u, c in DEFAULT_VARIABLES)

    drivers = drivers or {r.reactor_id: make_driver(r.driver, r.reactor_id, r.address) for r in settings.reactors}
    app_settings = AppSettings(sessions)
    notifier = Notifier(app_settings)
    acq = Acquisition(drivers, sessions, settings.sampling_interval_s, clock=clock,
                      alert_params=app_settings.alert_params(), notifier=notifier)
    backups = BackupManager(db_file, app_settings)

    def _apply(section: str, _value: dict) -> None:
        if section == "alerts":          # new thresholds apply to running experiments at once
            acq.alert_params = app_settings.alert_params()
            for st in acq.alert_states.values():
                st.params = acq.alert_params
    app_settings.on_change(_apply)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_acquisition:
            acq.start()
            backups.start()
        yield
        acq.stop()
        backups.stop()

    app = FastAPI(title="Fermentation Manager", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.sessions, app.state.acq, app.state.clock = settings, sessions, acq, clock
    app.state.app_settings, app.state.notifier, app.state.backups = app_settings, notifier, backups
    app.state.auth_secret = users_mod.load_secret(db_file.parent / "auth-secret.txt")
    app.state.pin_throttle = users_mod.Throttle()
    app.include_router(router, prefix="/api")
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the built React app (frontend/dist) with a fallback to index.html for client-side routes."""
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        return
    class HashedAssets(StaticFiles):
        """Build outputs have content hashes in their names: safe to cache for a long time."""
        async def get_response(self, path, scope):
            r = await super().get_response(path, scope)
            if r.status_code == 200:
                r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return r

    app.mount("/assets", HashedAssets(directory=dist / "assets"), name="assets")
    # index.html (and the other un-hashed files) must be revalidated on every load, otherwise phones
    # keep showing the previous build after an update.
    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f, headers=no_cache)
        return FileResponse(index, headers=no_cache)


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


def current_user(request: Request, db: Session) -> Optional[User]:
    """The signed-in user from the X-FM2-Token header (None = not signed in)."""
    tok = request.headers.get("x-fm2-token", "")
    parsed = users_mod.parse_token(request.app.state.auth_secret, tok) if tok else None
    if not parsed:
        return None
    u = db.get(User, parsed[0])
    return u if u and not u.archived and u.token_version == parsed[1] else None


def who(request: Request, db: Session) -> str:
    """Name recorded on data: the signed-in user, or Guest."""
    u = current_user(request, db)
    return u.name if u else GUEST_NAME


def _event(db: Session, e: Experiment, kind: str, message: str, t_h: Optional[float] = None) -> None:
    db.add(Event(experiment_id=e.id, reactor_id=e.reactor_id, kind=kind, severity="info", message=message, t_h=t_h))


def channels_of(e: Experiment) -> list[str]:
    return [c for c in CHANNELS if c in (e.channels or DEFAULT_CHANNELS)]


def _check_channels(ch: Optional[list[str]]) -> Optional[list[str]]:
    if ch is None:
        return None
    bad = [c for c in ch if c not in CHANNELS]
    if bad:
        raise HTTPException(422, f"Unknown channel(s) {bad}; allowed: {list(CHANNELS)}")
    return [c for c in CHANNELS if c in ch]


def _exp_out(e: Experiment) -> S.ExperimentOut:
    out = S.ExperimentOut.model_validate(e)
    out.status = e.status.value
    out.channels = channels_of(e)
    out.archived = bool(e.archived)
    sess = object_session(e)
    if sess is not None:
        out.n_sessions = sess.scalar(select(func.count(func.distinct(SessionMember.session_id)))
                                     .where(SessionMember.experiment_id == e.id)) or 0
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
        return [_named_out(db, o) for o in db.scalars(q)]

    @router.post(f"/{path}", response_model=S.NamedOut, status_code=201, name=f"create_{path}")
    def _create(body: S.NamedIn, db: Session = Depends(get_db)):
        if db.scalar(select(model).where(model.name == body.name)):
            raise HTTPException(409, f"'{body.name}' already exists")
        obj = model(name=body.name, description=body.description)
        db.add(obj)
        db.flush()
        return _named_out(db, obj)

    @router.post(f"/{path}/{{item_id}}/archive", response_model=S.NamedOut, name=f"archive_{path}")
    def _archive(item_id: int, db: Session = Depends(get_db)):
        obj = _404(db.get(model, item_id))
        obj.archived = True  # used items are archived, never deleted
        return _named_out(db, obj)

    @router.put(f"/{path}/{{item_id}}", response_model=S.NamedOut, name=f"update_{path}")
    def _update(item_id: int, body: S.NamedPatch, db: Session = Depends(get_db)):
        obj = _404(db.get(model, item_id))
        if body.name is not None:
            name = body.name.strip()
            if not name or db.scalar(select(model).where(model.name == name, model.id != item_id)):
                raise HTTPException(409, "Name empty or already used")
            obj.name = name
        if body.description is not None:
            obj.description = body.description
        if body.archived is not None:
            obj.archived = body.archived
        db.flush()
        return _named_out(db, obj)

    @router.delete(f"/{path}/{{item_id}}", name=f"delete_{path}")
    def _delete(item_id: int, db: Session = Depends(get_db)):
        obj = _404(db.get(model, item_id))
        n = _n_used(db, obj)
        if n:
            raise HTTPException(409, f"'{obj.name}' is used by {n} experiment(s): archive it instead.")
        db.delete(obj)
        return {"deleted": item_id}


def _n_used(db: Session, obj) -> int:
    col = Experiment.medium_id if isinstance(obj, Medium) else Experiment.buffer_id
    return db.scalar(select(func.count()).select_from(Experiment).where(col == obj.id)) or 0


def _named_out(db: Session, obj) -> S.NamedOut:
    out = S.NamedOut.model_validate(obj)
    out.n_used = _n_used(db, obj)
    return out


_catalogue("media", Medium)
_catalogue("buffers", Buffer)


VARIABLE_ROLES = ("biomass", "product", "substrate", "other")
FIT_ROLES = ("biomass", "product")   # growth-like (increasing) curves: the primary models apply


def _var_out(db: Session, v: Variable) -> S.VariableOut:
    n = db.scalar(select(func.count()).select_from(ManualMeasurement).where(ManualMeasurement.variable_id == v.id)) or 0
    return S.VariableOut(id=v.id, name=v.name, unit=v.unit, category=v.category.value, archived=bool(v.archived),
                         n_measurements=n)


def _role(value: str) -> VariableCategory:
    if value not in VARIABLE_ROLES:
        raise HTTPException(422, f"Role must be one of {', '.join(VARIABLE_ROLES)}")
    return VariableCategory(value)


@router.get("/variables", response_model=list[S.VariableOut])
def list_variables(db: Session = Depends(get_db), include_archived: bool = False):
    vs = db.scalars(select(Variable).order_by(Variable.name)).all()
    return [_var_out(db, v) for v in vs if include_archived or not v.archived]


@router.post("/variables", response_model=S.VariableOut, status_code=201)
def create_variable(body: S.VariableIn, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(Variable).where(func.lower(Variable.name) == name.lower())):
        raise HTTPException(409, f"A variable named {name} already exists")
    v = Variable(name=name, unit=body.unit.strip(), category=_role(body.category))
    db.add(v)
    db.flush()
    return _var_out(db, v)


@router.put("/variables/{vid}", response_model=S.VariableOut)
def update_variable(vid: int, body: S.VariablePatch, db: Session = Depends(get_db)):
    """Rename, change unit or role, archive. Measurements keep the unit they were entered with."""
    v = _404(db.get(Variable, vid))
    if body.name is not None:
        name = body.name.strip()
        if not name or db.scalar(select(Variable).where(func.lower(Variable.name) == name.lower(), Variable.id != vid)):
            raise HTTPException(409, "Name empty or already used")
        v.name = name
    if body.unit is not None:
        v.unit = body.unit.strip()
    if body.category is not None:
        v.category = _role(body.category)
    if body.archived is not None:
        v.archived = body.archived
    db.flush()
    return _var_out(db, v)


@router.delete("/variables/{vid}")
def delete_variable(vid: int, db: Session = Depends(get_db)):
    v = _404(db.get(Variable, vid))
    if _var_out(db, v).n_measurements:
        raise HTTPException(409, f"{v.name} has measurements: archive it instead.")
    db.delete(v)
    return {"deleted": vid}


@router.get("/profiles", response_model=list[S.ProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_profile_out(p) for p in db.scalars(select(ControllerProfile).order_by(ControllerProfile.name))]


def _profile_out(p: ControllerProfile) -> S.ProfileOut:
    out = S.ProfileOut.model_validate(p)
    out.ph_control_mode = p.ph_control_mode.value if p.ph_control_mode else None
    return out


@router.post("/profiles", response_model=S.ProfileOut, status_code=201)
def create_profile(body: S.ProfileIn, request: Request, db: Session = Depends(get_db)):
    data = body.model_dump()
    data["author"] = who(request, db)
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
    data["channels"] = _check_channels(data.get("channels"))
    data["author"] = who(request, db)
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
    if "channels" in data:
        data["channels"] = _check_channels(data["channels"])
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
    ranges = request.app.state.app_settings.setpoint_ranges()
    results = []
    chans = channels_of(e)
    for param, value in (("ph", prof.ph_setpoint), ("temperature", prof.temperature_setpoint),
                         ("stirrer", prof.stirrer_setpoint)):
        if value is None or param not in chans:
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
                               requested_value=value, readback_value=rb, status=status, origin="profile",
                               author=who(request, db)))
        results.append({"parameter": param, "status": status, "readback": rb})
    for param, lo, hi in (("ph", prof.ph_low_limit, prof.ph_high_limit),
                          ("temperature", prof.temperature_low_limit, prof.temperature_high_limit)):
        if (lo is None and hi is None) or param not in chans:
            continue
        try:
            for r in commands.write_limits(driver, param, lo, hi):
                db.add(SetpointCommand(experiment_id=e.id, reactor_id=e.reactor_id, parameter=r.parameter,
                                       old_value=r.old_value, requested_value=r.requested_value,
                                       readback_value=r.readback_value, status=r.status, message=r.message,
                                       origin="profile", author=who(request, db)))
                results.append({"parameter": r.parameter, "status": r.status, "readback": r.readback_value,
                                **({"message": r.message} if r.status != "confirmed" else {})})
        except commands.CommandRejected as ex:
            results.append({"parameter": f"{param}_limits", "status": "error", "message": str(ex)})
    if prof.ph_control_mode and "ph" in chans:
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
    chans = channels_of(e)
    if p:
        for param, planned in (("ph", p.ph_setpoint), ("temperature", p.temperature_setpoint),
                               ("stirrer", p.stirrer_setpoint)):
            if param not in chans:
                continue
            ctrl = snap.setpoint(param)
            rows.append({"parameter": f"{param}_setpoint", "plan": planned, "controller": ctrl,
                         "match": planned is None or (ctrl is not None and abs(ctrl - planned) < 1e-6)})
        if p.ph_control_mode and "ph" in chans:
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
        if param not in chans:
            continue
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
    _event(db, e, "started", f"{e.code} started by {who(request, db)}", 0.0)
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


def _close(exp_id: int, request: Request, db: Session, status: ExperimentStatus, note: str = "") -> S.ExperimentOut:
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.fermenting:
        raise HTTPException(409, "Experiment is not fermenting")
    e.status, e.ended_at = status, now_of(request)
    e.end_note = note.strip() or None
    t_h = (_as_utc(e.ended_at) - _as_utc(e.started_at)).total_seconds() / 3600 if e.started_at else None
    _event(db, e, "ended" if status == ExperimentStatus.completed else "aborted",
           f"{e.code} {'completed' if status == ExperimentStatus.completed else 'aborted'} by {who(request, db)}"
           + (f": {e.end_note}" if e.end_note else ""), t_h)
    return _exp_out(e)


@router.post("/experiments/{exp_id}/end", response_model=S.ExperimentOut)
def end_experiment(exp_id: int, request: Request, body: Optional[S.EndIn] = None, db: Session = Depends(get_db)):
    """End of experiment: outcome "completed" (end of production) or "aborted" (run failed), with an optional note."""
    body = body or S.EndIn()
    if body.outcome not in ("completed", "aborted"):
        raise HTTPException(422, "Outcome must be completed or aborted")
    status = ExperimentStatus.completed if body.outcome == "completed" else ExperimentStatus.aborted
    out = _close(exp_id, request, db, status, body.note)
    db.commit()
    cfg = request.app.state.app_settings.get("backup")
    if cfg["enabled"] and cfg["on_end_of_production"] and cfg["destination"]:
        request.app.state.backups.run_in_background(f"end of experiment {out.code} ({body.outcome})")
    return out


@router.post("/experiments/{exp_id}/abort", response_model=S.ExperimentOut)
def abort_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    return end_experiment(exp_id, request, S.EndIn(outcome="aborted"), db)


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
    """Controller readings, one column per field; optional-channel values become columns too."""
    rows = db.execute(select(ControllerReading).where(ControllerReading.experiment_id == exp_id,
                                                      ControllerReading.t_h >= since_h)
                      .order_by(ControllerReading.t_h)).scalars().all()
    cols = READING_FIELDS + list(EXTRA_FIELDS)
    df = pd.DataFrame([{f: getattr(r, f) for f in READING_FIELDS} | {k: (r.extra or {}).get(k) for k in EXTRA_FIELDS}
                       | {"ts": r.ts} for r in rows])
    return df if not df.empty else pd.DataFrame(columns=cols + ["ts"])


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
                          value=body.value, unit=body.unit or var.unit, note=body.note, author=who(request, db))
    db.add(m)
    db.flush()
    db.refresh(m)
    return _meas_out(m)


@router.patch("/measurements/{mid}", response_model=S.MeasurementOut)
def edit_measurement(mid: int, body: S.MeasurementPatch, request: Request, db: Session = Depends(get_db)):
    m = _404(db.get(ManualMeasurement, mid))
    old = {"value": m.value, "sampled_at": m.sampled_at.isoformat(), "unit": m.unit, "note": m.note}
    data = body.model_dump(exclude_unset=True, exclude={"author"})
    for k, v in data.items():
        setattr(m, k, v)
    if "sampled_at" in data:
        m.t_h = _t_h(db.get(Experiment, m.experiment_id), m.sampled_at)
    new = {"value": m.value, "sampled_at": _as_utc(m.sampled_at).isoformat(), "unit": m.unit, "note": m.note}
    db.add(ManualMeasurementEdit(measurement_id=m.id, old=old, new=new, author=who(request, db)))
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
                                                    "t_h": _t_h(exp, now), "channels": channels_of(exp),
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
    _check_remote(request, body.origin)
    try:
        res = commands.write_setpoint(driver, body.parameter, body.value,
                                      request.app.state.app_settings.setpoint_ranges(), alert_state)
    except commands.CommandRejected as ex:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id,
                               parameter=body.parameter, requested_value=body.value, status="rejected",
                               message=str(ex), origin=body.origin, author=who(request, db)))
        db.commit()
        raise HTTPException(422, str(ex))
    db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=res.parameter,
                           old_value=res.old_value, requested_value=res.requested_value,
                           readback_value=res.readback_value, status=res.status, message=res.message,
                           origin=body.origin, author=who(request, db)))
    return res.__dict__


def _check_remote(request: Request, origin: str) -> None:
    if origin == "remote" and not request.app.state.app_settings.get("remote")["allow_remote_setpoints"]:
        raise HTTPException(403, "Changes from remote devices are switched off (Settings › Remote access).")


@router.post("/reactors/{reactor_id}/limits")
def change_limits(reactor_id: str, body: S.LimitsIn, request: Request, db: Session = Depends(get_db)):
    """Change the process (alarm) limits of pH or temperature, with read-back; logged per limit."""
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id,
                                             Experiment.status == ExperimentStatus.fermenting))
    _check_remote(request, body.origin)
    try:
        results = commands.write_limits(driver, body.parameter, body.low, body.high)
    except commands.CommandRejected as ex:
        raise HTTPException(422, str(ex))
    for r in results:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=r.parameter,
                               old_value=r.old_value, requested_value=r.requested_value,
                               readback_value=r.readback_value, status=r.status, message=r.message,
                               origin=body.origin, author=who(request, db)))
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


def _json_safe(x):
    """NaN/inf (e.g. an undefined SD or a degenerate fit) become null: JSON has no NaN."""
    if isinstance(x, float):
        return x if np.isfinite(x) else None
    if isinstance(x, (np.floating, np.integer)):
        return _json_safe(x.item())
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x


def _fit(db: Session, exp_id: int, variable: str, model: str = "gompertz"):
    """(fit object or None, error message or None) for one manual variable."""
    t, y = _manual_series(db, exp_id, variable)
    if not len(t):
        return None, f"No {variable} measurements"
    try:
        return kinetics.fit_growth(t, y, model), None
    except ValueError as ex:
        return None, str(ex)


def experiment_parameters(db: Session, exp_id: int, fit_variable: str = "OD600", model: str = "gompertz",
                          bin_h: float = 0.25) -> dict:
    out: dict = {"fit_variable": fit_variable, "model": model, "fit": None, "fit_error": None}
    f, err = _fit(db, exp_id, fit_variable, model)
    out["fit"], out["fit_error"] = (f.summary() if f else None), err
    df = _readings_df(db, exp_id)
    t_od, od = _manual_series(db, exp_id, "OD600")
    final_od = float(od[-1]) if len(od) else None
    if not df.empty:
        bd = base_dosing.summarize(df["t_h"].astype(float), df["base_cumulative_ml"].astype(float), final_od, bin_h)
        out["base_dosing"] = bd.__dict__
    else:
        out["base_dosing"] = None
    return out


ANALYSIS_STEPS_H = (0.25, 0.5, 1.0, 2.0, 4.0)
OUT_OF_LIMIT_KINDS = ("out_of_limits", "out_of_limits_at_start")


def _measured_variables(db: Session, exp_id: int) -> list[Variable]:
    """Manual variables with at least one measurement, OD600 and AFU first."""
    vs = db.scalars(select(Variable).join(ManualMeasurement).where(ManualMeasurement.experiment_id == exp_id)
                    .distinct()).all()
    order = {"OD600": 0, "AFU": 1}
    return sorted(vs, key=lambda v: (order.get(v.name, 2), v.name))


def _partner(e: Experiment) -> Optional[dict]:
    if not e.parallel_run:
        return None
    for x in e.parallel_run.experiments:
        if x.id != e.id:
            return {"id": x.id, "code": x.code, "reactor_id": x.reactor_id, "status": x.status.value,
                    "parallel_run": e.parallel_run.code}
    return None


@router.get("/experiments/{exp_id}/analysis")
def experiment_analysis(exp_id: int, db: Session = Depends(get_db), fit_variable: str = "OD600",
                        bin_h: float = 0.25, model: str = "gompertz", step_h: float = 1.0):
    """
    Everything the single-experiment analysis shows: summary, manual points (mean ± SD per
    time point), model fits on every manual variable, controller series averaged over
    `step_h`, base-dosing metrics (peak rate on `bin_h` bins) and the rate bins.
    """
    e = _404(db.get(Experiment, exp_id))
    if model not in kinetics.MODELS:
        raise HTTPException(422, f"Unknown model '{model}'")
    if not (0.05 <= step_h <= 24) or not (0.05 <= bin_h <= 24):
        raise HTTPException(422, "Interval out of range")
    res = experiment_parameters(db, exp_id, fit_variable, model, bin_h)
    df = _readings_df(db, exp_id)
    res["rate_bins"] = [] if df.empty else [b.__dict__ for b in base_dosing.rate_bins(
        df["t_h"].astype(float), df["base_cumulative_ml"].astype(float), bin_h)]
    ch = channels_of(e)
    manual, fits = {}, {}
    for v in _measured_variables(db, exp_id):
        t, y = _manual_series(db, exp_id, v.name)
        manual[v.name] = {"unit": v.unit, "category": v.category.value, **report.grouped_points(t, y)}
        if v.category.value not in FIT_ROLES:
            fits[v.name] = {"error": f"Not fitted: {v.category.value} variables are not growth curves "
                                     "(change the role in Library › Variables).", "skipped": True}
            continue
        f, err = _fit(db, exp_id, v.name, model)
        fits[v.name] = {"params": f.summary(), "curve": f.curve()} if f else {"error": err}
    keys = [sd.key for sd in report.CONTROLLER_SERIES if sd.channel in ch]
    recorded_h = float(df["t_h"].max()) if not df.empty else None
    if e.started_at and e.ended_at:
        recorded_h = (_as_utc(e.ended_at) - _as_utc(e.started_at)).total_seconds() / 3600
    bd = res["base_dosing"] or {}
    od, afu = manual.get("OD600"), manual.get("AFU")
    n_out = db.scalar(select(func.count()).select_from(Event).where(
        Event.experiment_id == exp_id, Event.kind.in_(OUT_OF_LIMIT_KINDS))) or 0
    res.update({
        "experiment": {"id": e.id, "code": e.code, "name": e.name, "strain": e.strain, "reactor_id": e.reactor_id,
                       "status": e.status.value, "started_at": e.started_at, "ended_at": e.ended_at,
                       "channels": ch, "partner": _partner(e)},
        "summary": {"recorded_h": recorded_h,
                    "final_od": od["mean"][-1] if od and od["mean"] else None,
                    "max_afu": max(afu["mean"]) if afu and afu["mean"] else None,
                    "total_base_ml": bd.get("total_ml") if "ph" in ch else None,
                    "peak_base_rate_ml_min": bd.get("peak_rate_ml_min") if "ph" in ch else None,
                    "out_of_limit_events": int(n_out)},
        "models": {k: v[0] for k, v in kinetics.MODELS.items()},
        "manual": manual, "fits": fits, "step_h": step_h, "bin_h": bin_h,
        "series_defs": [{"key": sd.key, "label": sd.label, "unit": sd.unit} for sd in report.CONTROLLER_SERIES
                        if sd.key in keys],
        "series": report.binned_series(df, step_h, keys),
    })
    if "ph" not in ch:
        res["base_dosing"] = None
    return _json_safe(res)


PANEL_COLORS = {"OD600": "#1d3786", "AFU": "#7a4fa0", "ph": "#1d3786", "base_rate": "#1f7a63",
                "base_cumulative_ml": "#1f7a63", "temperature": "#b3261e", "stirrer": "#54565a", "do2": "#00639b"}
MANUAL_COLORS = ["#8a5300", "#006a6a", "#5c4d00", "#7d2a52"]


@router.get("/experiments/{exp_id}/figure.{fmt}")
def experiment_figure(exp_id: int, fmt: str, vars: str = "OD600,AFU,ph,base_rate", model: str = "gompertz",
                      step_h: float = 1.0, width_mm: float = 174, dpi: int = 300, theme: str = "grey",
                      fit: bool = True, db: Session = Depends(get_db)):
    """Time-course figure of the selected panels (manual variables by name, controller series by key)."""
    e = _404(db.get(Experiment, exp_id))
    if fmt not in report.FIG_FORMATS:
        raise HTTPException(404, "Format not available")
    if not (40 <= width_mm <= 500) or not (72 <= dpi <= 1200):
        raise HTTPException(422, "Width must be 40-500 mm and resolution 72-1200 dpi")
    an = experiment_analysis(exp_id, db, model=model, step_h=step_h)
    labels = {d["key"]: d for d in an["series_defs"]}
    panels, other = [], 0
    for v in [x.strip() for x in vars.split(",") if x.strip()]:
        if v in an["manual"]:
            m = an["manual"][v]
            col = PANEL_COLORS.get(v) or MANUAL_COLORS[other % len(MANUAL_COLORS)]
            other += v not in PANEL_COLORS
            curve = an["fits"][v].get("curve") if fit else None
            panels.append(report.Panel(f"{v} ({m['unit']})" if m["unit"] else v, col, points=m,
                                       curve=curve and {"t": curve["t"], "y": curve["y"]}))
        elif v in labels:
            d = labels[v]
            panels.append(report.Panel(f"{d['label']} ({d['unit']})" if d["unit"] else d["label"],
                                       PANEL_COLORS.get(v, "#54565a"),
                                       line={"t": an["series"]["t"], "y": an["series"][v]}, step=v == "base_rate"))
        else:
            raise HTTPException(422, f"Unknown variable '{v}'")
    try:
        data = report.render_figure(panels, fmt, width_mm, dpi, theme)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return StreamingResponse(io.BytesIO(data), media_type=report.FIG_FORMATS[fmt],
                             headers={"Content-Disposition": f'attachment; filename="{e.code}-time-course.{fmt}"'})


SESSION_PARAMS = [  # (key, label, unit, source); "fit" keys come from the model fit on the session's variable
    ("lag_h", "Lag time λ", "h", "fit"),
    ("mu_max_per_h", "Maximum specific growth rate μmax", "h⁻¹", "fit"),
    ("doubling_time_h", "Doubling time at μmax", "h", "fit"),
    ("t_mu_max_h", "Time at μmax", "h", "fit"),
    ("X_max", "Predicted maximum X₀·e^A", "", "fit"),
    ("A_ln", "Asymptote A", "ln X/X₀", "fit"),
    ("total_ml", "Total base", "mL", "controller, cumulative at end"),
    ("peak_rate_ml_min", "Peak base rate", "mL min⁻¹", "15-min bins"),
    ("t_peak_h", "Time of peak base rate", "h", "15-min bins"),
    ("t_half_total_h", "Time to 50 % of total base", "h", "controller, cumulative"),
    ("last_hour_rate_ml_min", "Base rate in the last hour", "mL min⁻¹", "slowdown at end of run"),
    ("base_per_final_od", "Total base per final OD600", "mL AU⁻¹", "acid produced per biomass, proxy"),
]
SESSION_CTRL_VARS = {"ph": ("pH", ""), "temperature": ("T", "°C"), "base_cumulative_ml": ("Base, cumulative", "mL"),
                     "base_rate": ("Base rate", "mL min⁻¹"), "stirrer": ("Stirrer", "rpm"), "do2": ("dO2", "%")}


def _check_session_body(db: Session, body: S.SessionIn) -> list[str]:
    if not body.name.strip():
        raise HTTPException(422, "Give the session a name.")
    if body.model not in kinetics.MODELS:
        raise HTTPException(422, f"Unknown model '{body.model}'")
    conds = [c.strip() for c in (body.conditions or []) if c.strip()]
    for c in body.members.values():
        if c.strip() and c.strip() not in conds:
            conds.append(c.strip())
    if len(set(conds)) != len(conds):
        raise HTTPException(422, "Condition names must be different.")
    for eid in body.members:
        e = _404(db.get(Experiment, eid), f"Experiment {eid} not found")
        if e.status != ExperimentStatus.completed:
            raise HTTPException(422, f"{e.code} is not completed")
    return conds


def _session_out(sess: AnalysisSession) -> dict:
    conds = list(sess.conditions or [])
    for m in sess.members:
        if m.condition not in conds:
            conds.append(m.condition)
    return {"id": sess.id, "code": sess.code, "name": sess.name, "fit_variable": sess.fit_variable,
            "model": sess.model if sess.model in kinetics.MODELS else "gompertz", "author": sess.author,
            "created_at": sess.created_at, "updated_at": sess.updated_at, "conditions": conds,
            "members": {m.experiment_id: m.condition for m in sess.members},
            "n_by_condition": {c: sum(1 for m in sess.members if m.condition == c) for c in conds}}


@router.get("/analysis-sessions")
def list_sessions(db: Session = Depends(get_db)):
    return [_session_out(x) for x in db.scalars(select(AnalysisSession).order_by(AnalysisSession.updated_at.desc()))]


@router.post("/analysis-sessions", status_code=201)
def create_session(body: S.SessionIn, request: Request, db: Session = Depends(get_db)):
    conds = _check_session_body(db, body)
    sess = AnalysisSession(code=_next_code(db, AnalysisSession, "AS"), name=body.name.strip(),
                           fit_variable=body.fit_variable, model=body.model, author=who(request, db), conditions=conds)
    for eid, cond in body.members.items():
        if cond.strip():
            sess.members.append(SessionMember(experiment_id=eid, condition=cond.strip()))
    db.add(sess)
    db.flush()
    return _session_out(sess)


@router.get("/analysis-sessions/{sid}")
def get_session(sid: int, db: Session = Depends(get_db)):
    return _session_out(_404(db.get(AnalysisSession, sid)))


@router.put("/analysis-sessions/{sid}")
def update_session(sid: int, body: S.SessionIn, db: Session = Depends(get_db)):
    sess = _404(db.get(AnalysisSession, sid))
    conds = _check_session_body(db, body)
    sess.name, sess.fit_variable, sess.model, sess.conditions = body.name.strip(), body.fit_variable, body.model, conds
    sess.members.clear()
    db.flush()
    for eid, cond in body.members.items():
        if cond.strip():
            sess.members.append(SessionMember(experiment_id=eid, condition=cond.strip()))
    sess.updated_at = _utcnow()
    db.flush()
    return _session_out(sess)


@router.delete("/analysis-sessions/{sid}")
def delete_session(sid: int, db: Session = Depends(get_db)):
    sess = _404(db.get(AnalysisSession, sid))
    code = sess.code
    db.delete(sess)
    db.flush()
    return {"deleted": code}


def _experiment_values(db: Session, e: Experiment, fit_variable: str, model: str) -> dict:
    """Per-experiment parameters used in the comparison (None where not available)."""
    f, err = _fit(db, e.id, fit_variable, model)
    p = f.summary() if f else {}
    df = _readings_df(db, e.id)
    t_od, od = _manual_series(db, e.id, "OD600")
    bd = {}
    if "ph" in channels_of(e) and not df.empty:
        bd = base_dosing.summarize(df["t_h"].astype(float), df["base_cumulative_ml"].astype(float),
                                   float(od[-1]) if len(od) else None).__dict__
    vals = {k: (p.get(k) if src == "fit" else bd.get(k)) for k, _, _, src in SESSION_PARAMS}
    return {"values": vals, "fit_error": err, "fit_warnings": p.get("warnings", [])}


def _session_curve(db: Session, ids: list[int], variable: str, step_h: float) -> Optional[dict]:
    """
    Mean ± SD per condition on a fixed time grid.
    Manual variables: each experiment interpolated on the grid, within the overlap of the replicates.
    Controller variables: each experiment averaged per interval first, then mean ± SD across experiments
    for intervals present in all of them.
    """
    if variable in SESSION_CTRL_VARS:
        per = []
        for eid in ids:
            df = _readings_df(db, eid)
            b = report.binned_series(df, step_h, [variable])
            per.append({round(t, 6): v for t, v in zip(b["t"], b[variable]) if v is not None})
        common = sorted(set.intersection(*(set(x) for x in per))) if per else []
        if not common:
            return None
        Y = np.array([[x[t] for t in common] for x in per])
        sd = Y.std(axis=0, ddof=1) if len(per) > 1 else np.full(len(common), np.nan)
        return {"t": common, "mean": Y.mean(axis=0).tolist(), "sd": [None if np.isnan(v) else float(v) for v in sd],
                "n": len(per), "warning": None if len(per) > 1 else "Only one experiment: no standard deviation."}
    rc = stats.replicate_curve([_manual_series(db, eid, variable) for eid in ids], step_h=step_h)
    if rc is None:
        return None
    return {"t": rc.t.tolist(), "mean": rc.mean.tolist(), "sd": [None if np.isnan(x) else float(x) for x in rc.sd.tolist()],
            "n": rc.n, "warning": rc.warning}


def _pooled_fit(db: Session, ids: list[int], variable: str, model: str) -> Optional[dict]:
    """Model curve for a condition: one fit on the pooled readings of its replicates, each normalised to its own X0."""
    ts, ys, x0s = [], [], []
    for eid in ids:
        t, y = _manual_series(db, eid, variable)
        m = np.isfinite(y) & (y > 0)
        t, y = t[m], y[m]
        if len(t):
            o = np.argsort(t)
            t, y = t[o], y[o]
            ts.append(t); ys.append(y / y[0]); x0s.append(y[0])
    if not ts:
        return None
    try:
        f = kinetics.fit_growth(np.concatenate(ts), np.concatenate(ys), model)
    except ValueError:
        return None
    x0 = float(np.mean(x0s))
    return {"t": f.t_fit.tolist(), "y": (np.asarray(f.X_fit) * x0).tolist()}


def session_results_data(db: Session, sid: int, step_h: float = 2.0, variable: Optional[str] = None) -> dict:
    sess = _404(db.get(AnalysisSession, sid))
    out = _session_out(sess)
    model, fv = out["model"], sess.fit_variable
    variable = variable or fv
    by_cond: dict[str, list[int]] = {c: [] for c in out["conditions"]}
    for m in sess.members:
        by_cond.setdefault(m.condition, []).append(m.experiment_id)
    exps = {e.id: e for e in db.scalars(select(Experiment).where(Experiment.id.in_(out["members"].keys())))}
    per_exp = {eid: _experiment_values(db, e, fv, model) for eid, e in exps.items()}
    fv_unit = db.scalar(select(Variable.unit).where(Variable.name == fv)) or ""
    comparisons = []
    for key, label, unit, src in SESSION_PARAMS:
        cmp = stats.compare_conditions({c: [per_exp[e]["values"][key] for e in ids]
                                        for c, ids in by_cond.items() if ids})
        comparisons.append({
            "key": key, "label": label, "unit": fv_unit if key == "X_max" else unit,
            "source": f"model fit on {fv}" if src == "fit" else src,
            "test": cmp.test, "statistic": cmp.statistic, "p_value": cmp.p_value, "difference": cmp.difference,
            "ci95": cmp.ci95, "posthoc": cmp.posthoc, "warning": cmp.warning,
            "conditions": {c: x.__dict__ for c, x in cmp.conditions.items()}})
    is_manual = variable not in SESSION_CTRL_VARS
    cats = {v.name: v.category.value for v in db.scalars(select(Variable))}
    curves = {}
    for c, ids in by_cond.items():
        if not ids:
            continue
        curve = _session_curve(db, ids, variable, step_h)
        if curve is not None and is_manual and cats.get(variable) in FIT_ROLES:
            curve["fit"] = _pooled_fit(db, ids, variable, model)
        curves[c] = curve
    runs: dict[str, dict] = {}
    for e in exps.values():
        if e.parallel_run:
            r = runs.setdefault(e.parallel_run.code, {"parallel_run": e.parallel_run.code, "experiments": [],
                                                      "date": e.started_at.date().isoformat() if e.started_at else None})
            r["experiments"].append({"code": e.code, "condition": out["members"][e.id]})
    flags = [dict(r, same_condition=len({x["condition"] for x in r["experiments"]}) == 1)
             for r in runs.values() if len(r["experiments"]) > 1]
    manual_vars = sorted({v.name for eid in exps for v in _measured_variables(db, eid)},
                         key=lambda n: ({"OD600": 0, "AFU": 1}.get(n, 2), n))
    units = {v.name: v.unit for v in db.scalars(select(Variable))}
    var_opts = [{"key": n, "label": n, "unit": units.get(n, ""), "manual": True, "category": cats.get(n),
                 "fit": cats.get(n) in FIT_ROLES} for n in manual_vars]
    var_opts += [{"key": k, "label": l, "unit": u, "manual": False, "category": None, "fit": False} for k, (l, u) in SESSION_CTRL_VARS.items()
                 if any(k in channels_of(e) or (k.startswith("base") and "ph" in channels_of(e)) for e in exps.values())]
    experiments = [{"id": e.id, "code": e.code, "name": e.name, "strain": e.strain, "reactor_id": e.reactor_id,
                    "date": e.started_at.date().isoformat() if e.started_at else None,
                    "parallel_run": e.parallel_run.code if e.parallel_run else None,
                    "condition": out["members"][e.id], **per_exp[e.id]} for e in exps.values()]
    experiments.sort(key=lambda x: (out["conditions"].index(x["condition"]) if x["condition"] in out["conditions"] else 99,
                                    x["code"]))
    return {**out, "models": {k: v[0] for k, v in kinetics.MODELS.items()},
            "conditions_n": {c: len(ids) for c, ids in by_cond.items()},
            "comparisons": comparisons, "variable": variable, "variable_options": var_opts, "step_h": step_h,
            "curves": curves, "parallel_run_flags": flags, "experiments": experiments}


@router.get("/analysis-sessions/{sid}/results")
def session_results(sid: int, db: Session = Depends(get_db), step_h: float = 2.0, variable: Optional[str] = None):
    if not (0.05 <= step_h <= 24):
        raise HTTPException(422, "Interval out of range")
    res = session_results_data(db, sid, step_h, variable)
    # backwards-compatible shape: comparisons keyed by parameter, conditions -> n
    res["comparisons_by_key"] = {c["key"]: c for c in res["comparisons"]}
    return _json_safe(res)


SESSION_COLORS = ["#1d3786", "#c98a00", "#1f7a63", "#7a4fa0", "#b3261e", "#54565a"]
SESSION_MARKERS = ["o", "^", "s", "D", "v", "P"]


@router.get("/analysis-sessions/{sid}/figure.{fmt}")
def session_figure(sid: int, fmt: str, variable: Optional[str] = None, step_h: float = 2.0, width_mm: float = 174,
                   dpi: int = 300, theme: str = "grey", fit: bool = True, db: Session = Depends(get_db)):
    if fmt not in report.FIG_FORMATS:
        raise HTTPException(404, "Format not available")
    if not (40 <= width_mm <= 500) or not (72 <= dpi <= 1200):
        raise HTTPException(422, "Width must be 40-500 mm and resolution 72-1200 dpi")
    res = session_results_data(db, sid, step_h, variable)
    opt = next((o for o in res["variable_options"] if o["key"] == res["variable"]), None)
    if opt is None:
        raise HTTPException(422, f"No data for '{res['variable']}' in this session")
    letters = "ABCDEFGHIJ"
    series = []
    for i, c in enumerate(res["conditions"]):
        cv = res["curves"].get(c)
        if not cv:
            continue
        series.append(report.ConditionSeries(f"{letters[i]} · {c} (n = {cv['n']})", SESSION_COLORS[i % 6],
                                             SESSION_MARKERS[i % 6], cv["t"], cv["mean"], cv["sd"],
                                             (cv.get("fit") if fit else None)))
    ylabel = f"{opt['label']} ({opt['unit']})" if opt["unit"] else opt["label"]
    try:
        data = report.render_conditions(series, ylabel, fmt, width_mm, dpi, theme)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return StreamingResponse(io.BytesIO(data), media_type=report.FIG_FORMATS[fmt], headers={
        "Content-Disposition": f'attachment; filename="{res["code"]}-{res["variable"]}.{fmt}"'})


@router.get("/analysis-sessions/{sid}/export.{fmt}")
def session_export(sid: int, fmt: str, step_h: float = 2.0, db: Session = Depends(get_db)):
    """Experiments with conditions, per-experiment parameters, comparisons and mean ± SD curves of every variable."""
    if fmt not in ("xlsx", "zip"):
        raise HTTPException(404, "Format not available")
    res = session_results_data(db, sid, step_h)
    letters = "ABCDEFGHIJ"
    label = {c: f"{letters[i]} · {c}" for i, c in enumerate(res["conditions"])}
    tables: dict[str, pd.DataFrame] = {}
    tables["Experiments"] = pd.DataFrame([{
        "condition": label.get(x["condition"], x["condition"]), "code": x["code"], "name": x["name"],
        "strain": x["strain"], "reactor": x["reactor_id"], "date": x["date"], "parallel_run": x["parallel_run"],
        **{k: x["values"][k] for k, *_ in SESSION_PARAMS},
        "fit_error": x["fit_error"], "fit_warnings": "; ".join(x["fit_warnings"])} for x in res["experiments"]])
    rows = []
    for c in res["comparisons"]:
        r = {"parameter": c["label"], "unit": c["unit"], "source": c["source"]}
        for cond in res["conditions"]:
            s_ = c["conditions"].get(cond) or {}
            r[f"{label[cond]} n"] = s_.get("n")
            r[f"{label[cond]} mean"] = s_.get("mean")
            r[f"{label[cond]} SD"] = s_.get("sd")
        r.update({"test": c["test"], "statistic": c["statistic"], "p": c["p_value"], "difference": c["difference"],
                  "CI95 low": c["ci95"][0] if c["ci95"] else None, "CI95 high": c["ci95"][1] if c["ci95"] else None,
                  "post hoc (Tukey)": "; ".join(f"{p['a']}−{p['b']}: Δ={p['difference']:.4g}, p_adj={p['p_adj']:.3g}"
                                              for p in c["posthoc"]),
                  "note": c["warning"]})
        rows.append(r)
    tables["Comparisons"] = pd.DataFrame(rows)
    crow = []
    for opt in res["variable_options"]:
        r2 = session_results_data(db, sid, step_h, opt["key"])
        for cond, cv in r2["curves"].items():
            for t, m, s_ in zip(cv["t"], cv["mean"], cv["sd"]) if cv else []:
                crow.append({"variable": opt["label"], "unit": opt["unit"], "condition": label.get(cond, cond),
                             "t_h": t, "mean": m, "SD": s_, "n": cv["n"]})
    tables["Mean curves"] = pd.DataFrame(crow, columns=["variable", "unit", "condition", "t_h", "mean", "SD", "n"])
    tables["Session"] = pd.DataFrame([{"field": k, "value": v} for k, v in {
        "code": res["code"], "name": res["name"], "fit variable": res["fit_variable"],
        "model": res["models"][res["model"]], "curve interval (h)": step_h,
        "parallel runs": "; ".join(f"{f['parallel_run']}: " + ", ".join(f"{x['code']} ({x['condition']})"
                                                                         for x in f["experiments"])
                                   for f in res["parallel_run_flags"]) or None}.items()])
    buf = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for name, df in tables.items():
                df.to_excel(xw, sheet_name=name, index=False)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        import zipfile
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, df in tables.items():
                z.writestr(f"{res['code']}_{name.lower().replace(' ', '_')}.csv", df.to_csv(index=False))
        media = "application/zip"
    buf.seek(0)
    return StreamingResponse(buf, media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{res["code"]}.{fmt}"'})


# --- export -------------------------------------------------------------------------------

EXPORT_BLOCKS = ("controller", "manual", "parameters", "metadata")


def _flatten(d, prefix: str = "") -> list[tuple[str, object]]:
    out = []
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out += _flatten(v, key + ".")
        else:
            out.append((key, v if not isinstance(v, list) else ", ".join(map(str, v))))
    return out


def _naive(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df and not df.empty:
            df[c] = pd.to_datetime(df[c], utc=True).dt.tz_localize(None)
    return df


def export_tables(db: Session, exp_id: int, blocks=EXPORT_BLOCKS, resample_min: Optional[float] = None,
                  model: str = "gompertz") -> dict[str, pd.DataFrame]:
    """One table per export sheet/CSV. Times: hours since start plus absolute UTC timestamps."""
    e = _404(db.get(Experiment, exp_id))
    out: dict[str, pd.DataFrame] = {}
    if "controller" in blocks:
        r = _readings_df(db, exp_id)
        if resample_min and not r.empty:
            r = r.set_index(pd.to_datetime(r["ts"], utc=True)).drop(columns=["ts"]) \
                .apply(pd.to_numeric, errors="coerce").resample(f"{resample_min}min").mean() \
                .dropna(how="all").reset_index()
        r = _naive(r, ["ts"]).rename(columns={"ts": "timestamp (UTC)"})
        lead = [c for c in ("t_h", "timestamp (UTC)") if c in r]
        out["Controller data"] = r[lead + [c for c in r.columns if c not in lead]].dropna(axis=1, how="all") \
            if not r.empty else r
    if "manual" in blocks:
        m = pd.DataFrame([x.model_dump() for x in list_measurements(exp_id, db)])
        if not m.empty:
            m = _naive(m, ["sampled_at"]).rename(columns={"sampled_at": "sampled_at (UTC)"})
            m = m[["t_h", "sampled_at (UTC)", "variable_name", "value", "unit", "note", "author", "id"]]
        out["Manual variables"] = m
    if "parameters" in blocks:
        rows = []
        for v in _measured_variables(db, exp_id):
            f, err = _fit(db, exp_id, v.name, model)
            if f:
                rows += [{"block": f"kinetics {v.name}", "parameter": k,
                          "value": "; ".join(val) if isinstance(val, list) else val}
                         for k, val in f.summary().items()]
            else:
                rows.append({"block": f"kinetics {v.name}", "parameter": "error", "value": err})
        bd = experiment_parameters(db, exp_id, model=model)["base_dosing"] if "ph" in channels_of(e) else None
        rows += [{"block": "base dosing", "parameter": k, "value": v} for k, v in (bd or {}).items()]
        out["Parameters"] = pd.DataFrame(rows, columns=["block", "parameter", "value"])
    if "metadata" in blocks:
        meta = {"code": e.code, "name": e.name, "strain": e.strain, "reactor": e.reactor_id,
                "status": e.status.value, "channels": ", ".join(channels_of(e)),
                "started_at (UTC)": e.started_at, "ended_at (UTC)": e.ended_at,
                "parallel_run": e.parallel_run.code if e.parallel_run else None,
                "medium": e.medium.name if e.medium else None, "buffer": e.buffer.name if e.buffer else None,
                "working_volume_ml": e.working_volume_ml, "author": e.author, "notes": e.notes}
        rows = [{"field": k, "value": v} for k, v in meta.items()]
        rows += [{"field": f"plan.{k}", "value": v} for k, v in _flatten(e.plan_snapshot)]
        md = pd.DataFrame(rows)
        md["value"] = md["value"].map(lambda v: v.replace(tzinfo=None) if isinstance(v, datetime) else v)
        out["Metadata"] = md
        log = db.scalars(select(SetpointCommand).where(SetpointCommand.experiment_id == exp_id)
                         .order_by(SetpointCommand.at)).all()
        out["Change log"] = _naive(pd.DataFrame([{
            "at (UTC)": c.at, "parameter": c.parameter, "old_value": c.old_value, "requested_value": c.requested_value,
            "readback_value": c.readback_value, "status": c.status, "origin": c.origin, "author": c.author,
            "message": c.message} for c in log], columns=["at (UTC)", "parameter", "old_value", "requested_value",
                                                          "readback_value", "status", "origin", "author", "message"]),
            ["at (UTC)"])
        ev = db.scalars(select(Event).where(Event.experiment_id == exp_id).order_by(Event.at)).all()
        out["Events"] = _naive(pd.DataFrame([{"at (UTC)": x.at, "t_h": x.t_h, "kind": x.kind, "severity": x.severity,
                                              "message": x.message} for x in ev],
                                            columns=["at (UTC)", "t_h", "kind", "severity", "message"]), ["at (UTC)"])
    return out


@router.get("/experiments/{exp_id}/export.{fmt}")
def export_data(exp_id: int, fmt: str, db: Session = Depends(get_db), resample_min: Optional[float] = None,
                blocks: str = ",".join(EXPORT_BLOCKS), model: str = "gompertz"):
    """XLSX with one sheet per block, or a zip of CSV files."""
    e = _404(db.get(Experiment, exp_id))
    if fmt not in ("xlsx", "zip"):
        raise HTTPException(404, "Format not available")
    sel = [b for b in blocks.split(",") if b]
    if not sel or any(b not in EXPORT_BLOCKS for b in sel):
        raise HTTPException(422, f"Blocks must be among {', '.join(EXPORT_BLOCKS)}")
    if resample_min is not None and not (0.05 <= resample_min <= 1440):
        raise HTTPException(422, "Resampling interval out of range")
    tables = export_tables(db, exp_id, sel, resample_min, model)
    buf = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for name, df in tables.items():
                df.to_excel(xw, sheet_name=name[:31], index=False)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        import zipfile
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, df in tables.items():
                z.writestr(f"{e.code}_{name.lower().replace(' ', '_')}.csv", df.to_csv(index=False))
        media = "application/zip"
    buf.seek(0)
    return StreamingResponse(buf, media_type=media,
                             headers={"Content-Disposition": f'attachment; filename="{e.code}.{fmt}"'})


# --- users (identity for signatures, optional PIN) ---------------------------------------------

def _user_out(u: User) -> dict:
    return {"id": u.id, "name": u.name, "has_pin": u.pin_hash is not None, "archived": u.archived,
            "guest": u.name == GUEST_NAME}


@router.get("/users")
def list_users(db: Session = Depends(get_db), include_archived: bool = False):
    q = select(User).order_by(User.name)
    users = [u for u in db.scalars(q) if include_archived or not u.archived]
    return sorted([_user_out(u) for u in users], key=lambda x: (x["guest"], x["name"].lower()))


@router.post("/users", status_code=201)
def create_user(body: S.UserIn, request: Request, db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(422, "Give a name (up to 60 characters).")
    if db.scalar(select(User).where(func.lower(User.name) == name.lower())):
        raise HTTPException(409, f"A user named {name} already exists.")
    if body.pin and not users_mod.valid_pin(body.pin):
        raise HTTPException(422, f"The PIN must be {users_mod.PIN_MIN}-{users_mod.PIN_MAX} digits.")
    u = User(name=name, pin_hash=users_mod.hash_pin(body.pin) if body.pin else None)
    db.add(u)
    db.flush()
    return {"user": _user_out(u), "token": users_mod.make_token(request.app.state.auth_secret, u.id, u.token_version)}


@router.post("/auth/login")
def login(body: S.LoginIn, request: Request, db: Session = Depends(get_db)):
    u = db.get(User, body.user_id)
    if u is None or u.archived:
        raise HTTPException(404, "User not found")
    th: users_mod.Throttle = request.app.state.pin_throttle
    if u.pin_hash:
        wait = th.locked_for(u.id)
        if wait:
            raise HTTPException(429, f"Too many wrong PINs: try again in {int(wait // 60) + 1} min.")
        if not users_mod.check_pin(body.pin or "", u.pin_hash):
            th.fail(u.id)
            raise HTTPException(401, "Wrong PIN.")
        th.ok(u.id)
    return {"user": _user_out(u), "token": users_mod.make_token(request.app.state.auth_secret, u.id, u.token_version)}


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    u = current_user(request, db)
    return {"user": _user_out(u) if u else None}


@router.put("/users/{uid}")
def update_user(uid: int, body: S.UserPatch, request: Request, db: Session = Depends(get_db)):
    """Rename, set/change/remove the PIN or archive. A user with a PIN can be changed only when signed in as them."""
    u = _404(db.get(User, uid))
    me_ = current_user(request, db)
    if u.pin_hash and (me_ is None or me_.id != u.id):
        raise HTTPException(403, f"Sign in as {u.name} to change this user.")
    if u.name == GUEST_NAME and (body.name not in (None, GUEST_NAME) or body.pin or body.archived):
        raise HTTPException(422, "Guest cannot be renamed, archived or given a PIN.")
    token = None
    if body.name is not None and body.name.strip() != u.name:
        name = body.name.strip()
        if not name or db.scalar(select(User).where(func.lower(User.name) == name.lower(), User.id != u.id)):
            raise HTTPException(409, "Name empty or already used.")
        u.name = name   # past records keep the name they were signed with
    if body.pin is not None:
        if body.pin and not users_mod.valid_pin(body.pin):
            raise HTTPException(422, f"The PIN must be {users_mod.PIN_MIN}-{users_mod.PIN_MAX} digits.")
        u.pin_hash = users_mod.hash_pin(body.pin) if body.pin else None
        u.token_version += 1   # other devices signed in as this user must sign in again
        token = users_mod.make_token(request.app.state.auth_secret, u.id, u.token_version)
    if body.archived is not None:
        u.archived = body.archived
    db.flush()
    return {"user": _user_out(u), "token": token}


# --- settings, notifications, backup --------------------------------------------------------

@router.get("/settings")
def get_settings(request: Request):
    return request.app.state.app_settings.public()


@router.put("/settings/{section}")
def put_settings(section: str, body: dict, request: Request):
    try:
        request.app.state.app_settings.put(section, body)
    except SettingsError as ex:
        raise HTTPException(422, str(ex))
    return request.app.state.app_settings.public()[section]


@router.post("/settings/notifications/test")
def test_notification(request: Request):
    cfg = request.app.state.app_settings.get("notifications")
    try:
        notify_send(cfg, "FM2 test", "Test notification from Fermentation Manager 2.", "info")
    except NotifyError as ex:
        raise HTTPException(502, str(ex))
    return {"sent": True, "channel": cfg["channel"]}


@router.get("/backup/status")
def backup_status(request: Request):
    return request.app.state.backups.status()


@router.post("/backup/run")
def backup_run(request: Request):
    try:
        f = request.app.state.backups.run("manual")
    except BackupError as ex:
        raise HTTPException(502, str(ex))
    return {"file": str(f), **request.app.state.backups.status()}


@router.get("/system")
def system_info(request: Request, db: Session = Depends(get_db)):
    st: Settings = request.app.state.settings
    acq: Acquisition = request.app.state.acq
    return {
        "version": __version__, "data_dir": str(st.data_dir), "sampling_interval_s": acq.interval_s,
        "experiments": db.scalar(select(func.count()).select_from(Experiment)),
        "reactors": [{"reactor_id": r.reactor_id, "driver": r.driver, "address": r.address,
                      "online": acq.live[r.reactor_id].last_error is None and acq.live[r.reactor_id].last_ok is not None,
                      "capabilities": acq.drivers[r.reactor_id].capabilities.__dict__}
                     for r in st.reactors if r.reactor_id in acq.drivers],
    }


@router.get("/system/status")
def system_status():
    """Server (bioreactor PC) and remote-access state, for the Home page."""
    from ..system_status import remote_access, server_status
    return {"server": {**server_status(), "version": __version__}, "remote": remote_access()}
