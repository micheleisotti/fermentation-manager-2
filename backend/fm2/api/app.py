"""
FastAPI application. One service process hosts the API, the acquisition thread and
(later) the built web interface, so a single Windows service covers everything.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
import re
import os
import ipaddress
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.orm import Session, object_session

from ..build_info import build_info
from .. import __version__, citation
from ..acquisition import Acquisition, _as_utc
from ..alerts import AlertParams
from ..analysis import base_dosing, control, kinetics, report, stats
from ..services.app_settings import SWITCHABLE_LOOPS
from .. import loops
from ..config import DRIVERS, INTERVAL_MAX_S, INTERVAL_MIN_S, INTERVAL_WARN_S, Settings
import logging
from pathlib import Path

from ..backup import BackupError, BackupManager
from ..notify import Notifier, NotifyError, send as notify_send
from ..services.app_settings import AppSettings, SettingsError
from ..db import add_missing_columns, make_engine, make_session_factory, session_scope
from ..drivers.base import CHANNELS, DEFAULT_CHANNELS, DOSE_COUNTER_LABELS, DOSE_COUNTERS, EXTRA_FIELDS, counter_value
from ..drivers import ControllerDriver, DriverError, PhControl, make_driver
from ..models import (
    DEFAULT_VARIABLES, AnalysisSession, Base, Buffer, ControllerProfile, ControllerReading, Event, ExperimentTemplate,
    Experiment, ExperimentStatus, ManualMeasurement, ManualMeasurementEdit, Medium, ParallelRun,
    PhControlMode, Reactor, SessionMember, SetpointCommand, Variable, VariableCategory,
)
from ..services import commands
from .. import users as users_mod
from ..models import GUEST_NAME, User, utcnow
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

    given = drivers is not None
    reactors = _seed_reactors(sessions, settings, list(drivers) if given else None)
    if not given:
        drivers = {}
        for r in reactors:
            if r["enabled"]:
                try:
                    drivers[r["id"]] = make_driver(r["driver"], r["id"], r["address"], clock=None if clock is _utcnow else clock)
                except ValueError as ex:
                    log.error("bioreactor %s: %s", r["id"], ex)
    app_settings = AppSettings(sessions)
    notifier = Notifier(app_settings)
    acq = Acquisition(drivers, sessions, settings.sampling_interval_s, clock=clock,
                      alert_params=app_settings.alert_params(), notifier=notifier,
                      intervals={r["id"]: r["interval_s"] for r in reactors if r["id"] in drivers})
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
    @app.middleware("http")
    async def check_level(request: Request, call_next):
        need = needed_role(request.method, request.url.path)
        if need:
            s_ = sessions()
            try:
                have = role_of(request, s_)
            finally:
                s_.close()
            if users_mod.role_rank(have) < users_mod.role_rank(need):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": f"This needs the {users_mod.ROLE_LABELS[need]} level "
                                               f"(you are {users_mod.ROLE_LABELS[have]})."}, status_code=403)
        return await call_next(request)

    app.include_router(router, prefix="/api")
    _mount_frontend(app, settings)
    return app


def _seed_reactors(sessions, settings: Settings, given_ids: Optional[list[str]]) -> list[dict]:
    """
    Fill Settings > Bioreactors & probes on first start from the old configuration (FM2_R1_*, local.env),
    so updating keeps the same controllers and addresses. With drivers given (tests, demo clock) the
    table just mirrors their ids.
    """
    with session_scope(sessions) as s:
        if given_ids is not None:
            for i, rid in enumerate(given_ids):
                if s.get(Reactor, rid) is None:
                    s.add(Reactor(id=rid, name=f"Bioreactor {rid[1:] if rid[1:].isdigit() else rid}",
                                  driver="simulator", interval_s=settings.sampling_interval_s, position=i))
        elif not s.scalar(select(func.count()).select_from(Reactor)):
            for i, r in enumerate(settings.reactors):
                s.add(Reactor(id=r.reactor_id, name=f"Bioreactor {r.reactor_id[1:]}", driver=r.driver,
                              address=r.address, interval_s=settings.sampling_interval_s, position=i))
            log.info("bioreactors created from the previous configuration")
        s.flush()
        return [{"id": r.id, "driver": r.driver, "address": r.address, "interval_s": r.interval_s,
                 "enabled": r.enabled} for r in s.scalars(select(Reactor).order_by(Reactor.position, Reactor.id))]


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


def admins_exist(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.archived.is_(False))))


def effective_role(u: Optional[User], db: Session) -> str:
    """Level actually in force for a user (see users.py): admin/operator need a PIN, Guest is an observer."""
    if not admins_exist(db):
        return "admin"            # no administrator yet: levels not enforced (earlier behaviour)
    if u is None or u.name == GUEST_NAME or not u.pin_hash:
        return "observer"
    return u.role or "operator"


def role_of(request: Request, db: Session) -> str:
    return effective_role(current_user(request, db), db)


def require_role(request: Request, db: Session, needed: str) -> None:
    have = role_of(request, db)
    if users_mod.role_rank(have) < users_mod.role_rank(needed):
        raise HTTPException(403, f"This needs the {users_mod.ROLE_LABELS[needed]} level (you are {users_mod.ROLE_LABELS[have]}).")


# Level needed by each writing request, checked in one place (middleware) so no endpoint is forgotten.
# First match wins; any other POST/PUT/PATCH/DELETE under /api needs Operator. Reading (GET) is open.
ROLE_RULES: list[tuple[str, str, Optional[str]]] = [
    ("POST", r"^/api/auth/login$", None),
    ("POST", r"^/api/users$", None),                       # creating yourself (new users start as Operator)
    ("PUT", r"^/api/users/\d+$", None),                   # own name and PIN (checked in the endpoint)
    ("POST", r"^/api/users/\d+/first-admin$", None),      # checked in the endpoint (lab PC, no admin yet)
    ("PUT", r"^/api/users/\d+/role$", "admin"),
    ("PUT", r"^/api/settings/", "admin"),
    ("POST", r"^/api/settings/", "admin"),
    ("POST", r"^/api/backup/run$", "admin"),
    ("ANY", r"^/api/bioreactors", "admin"),
    ("DELETE", r"^/api/experiments/\d+$", "admin"),
]


def needed_role(method: str, path: str) -> Optional[str]:
    if method in ("GET", "HEAD", "OPTIONS") or not path.startswith("/api/"):
        return None
    for m, pattern, role in ROLE_RULES:
        if (m == "ANY" or m == method) and re.match(pattern, path):
            return role
    return "operator"


def who(request: Request, db: Session) -> str:
    """Name recorded on data: the signed-in user, or Guest."""
    u = current_user(request, db)
    return u.name if u else GUEST_NAME


def _event(db: Session, e: Experiment, kind: str, message: str, t_h: Optional[float] = None,
           severity: str = "info") -> None:
    db.add(Event(experiment_id=e.id, reactor_id=e.reactor_id, kind=kind, severity=severity, message=message, t_h=t_h))


def _reactor_event(db: Session, request: Request, reactor_id: str, exp: Optional[Experiment], kind: str,
                   message: str, severity: str = "info") -> None:
    """Event of a command on a reactor: in the experiment if one is active, else on the reactor itself."""
    if exp is not None:
        t_h = _t_h(exp, now_of(request)) if (exp.started_at or exp.prepared_at) else None
        _event(db, exp, kind, message, t_h, severity=severity)
    else:
        db.add(Event(experiment_id=None, reactor_id=reactor_id, kind=kind, severity=severity, message=message))


def channels_of(e: Experiment) -> list[str]:
    return [c for c in CHANNELS if c in (e.channels or DEFAULT_CHANNELS)]


def _check_channels(ch: Optional[list[str]]) -> Optional[list[str]]:
    if ch is None:
        return None
    bad = [c for c in ch if c not in CHANNELS]
    if bad:
        raise HTTPException(422, f"Unknown channel(s) {bad}; allowed: {list(CHANNELS)}")
    return [c for c in CHANNELS if c in ch]


PLAN_KEYS = ("ph_setpoint", "temperature_setpoint", "stirrer_setpoint", "ph_control_mode", "ph_pid",
             "ph_low_limit", "ph_high_limit", "temperature_low_limit", "temperature_high_limit",
             "temperature_control_mode", "temperature_pid", "loop_assignments", "channel_params")
LOOP_PLAN = {"ph": ("ph_control_mode", "ph_pid"), "temperature": ("temperature_control_mode", "temperature_pid")}


def profile_params(p: Optional[ControllerProfile]) -> dict:
    if p is None:
        return {}
    d = {k: getattr(p, k) for k in PLAN_KEYS}
    d["ph_control_mode"] = p.ph_control_mode.value if p.ph_control_mode else None
    d["ph_pid"] = dict(p.ph_pid) if p.ph_pid else None
    d["temperature_pid"] = dict(p.temperature_pid) if p.temperature_pid else None
    d["loop_assignments"] = dict(p.loop_assignments) if p.loop_assignments else None
    d["channel_params"] = dict(p.channel_params) if p.channel_params else None
    return d


CHANNEL_PARAM_KEYS = ("setpoint", "low", "high")


def _clean_channel_params(v) -> Optional[dict]:
    """{channel: {setpoint, low, high, mode, params{}}} for channels other than pH and temperature."""
    if not v:
        return None
    if not isinstance(v, dict):
        raise HTTPException(422, "channel_params must be an object")
    out = {}
    for ch, block in v.items():
        if ch not in CHANNELS or not isinstance(block, dict):
            raise HTTPException(422, f"Unknown channel {ch}")
        b: dict = {}
        for k in CHANNEL_PARAM_KEYS:
            x = block.get(k)
            b[k] = None if x in ("", None) else float(x)
        if b["low"] is not None and b["high"] is not None and b["low"] >= b["high"]:
            raise HTTPException(422, f"{ch}: low limit must be lower than high limit")
        mode = block.get("mode")
        b["mode"] = str(mode)[:30] if mode else None
        b["params"] = {str(kk)[:40]: (None if vv in ("", None) else float(vv)) for kk, vv in (block.get("params") or {}).items()} or None
        if any(x is not None for x in b.values()):
            out[ch] = b
    return out or None


def plan_of(e: Experiment) -> dict:
    """Controller parameters of the plan: the plan's own (edited) values, else the profile's."""
    if e.plan_params:
        return {k: e.plan_params.get(k) for k in PLAN_KEYS}
    return profile_params(e.profile)


def _clean_plan(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    out = {}
    for k in PLAN_KEYS:
        v = d.get(k)
        if k in ("ph_control_mode", "temperature_control_mode"):
            if v not in (None, "", "pid", "adaptive"):
                raise HTTPException(422, f"{k.split('_')[0]} control mode must be pid or adaptive")
            out[k] = v or None
        elif k == "loop_assignments":
            out[k] = _clean_assignments(v)
        elif k == "channel_params":
            out[k] = _clean_channel_params(v)
        elif k in ("ph_pid", "temperature_pid"):
            out[k] = {kk: (None if vv in ("", None) else float(vv)) for kk, vv in (v or {}).items()} or None
        else:
            out[k] = None if v in ("", None) else float(v)
    for a, b in (("ph_low_limit", "ph_high_limit"), ("temperature_low_limit", "temperature_high_limit")):
        if out[a] is not None and out[b] is not None and out[a] >= out[b]:
            raise HTTPException(422, f"{a.replace('_', ' ')} must be lower than {b.replace('_', ' ')}")
    return out


def _clean_assignments(v) -> Optional[dict]:
    """{loop: [{id, name, side, kind}]} as recorded from a controller (loops.assignments_of)."""
    if not v:
        return None
    if not isinstance(v, dict):
        raise HTTPException(422, "loop_assignments must be an object")
    out = {}
    for loop, items in v.items():
        if loop not in LOOP_PLAN or not isinstance(items, list):
            raise HTTPException(422, f"loop_assignments: unknown loop {loop!r}")
        try:
            out[loop] = [{"id": int(i["id"]), "name": str(i.get("name", "")), "side": int(i["side"]),
                          "kind": str(i.get("kind", "actuator"))} for i in items]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(422, f"loop_assignments: malformed entry for {loop}")
    return out


def _same(a, b) -> bool:
    """Equality of plan values: numbers with a tolerance, nested dicts and lists (loop assignments)."""
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        a, b = list(a or []), list(b or [])
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) or isinstance(b, dict):
        a, b = a or {}, b or {}
        return all(_same(a.get(k), b.get(k)) for k in set(a) | set(b))
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    return abs(float(a) - float(b)) < 1e-9


def _exp_out(e: Experiment) -> S.ExperimentOut:
    out = S.ExperimentOut.model_validate(e)
    out.status = e.status.value
    out.plan_params = plan_of(e) or None
    ref = e.profile_ref if e.profile else None
    out.profile_changed = bool(ref is not None and not _same(ref, profile_params(e.profile)))
    # "Custom": the plan's values differ from the profile as it was when the plan was saved
    out.profile_modified = bool(e.plan_params and e.profile and not _same(e.plan_params, ref if ref is not None else profile_params(e.profile)))
    out.profile_name = e.profile.name if e.profile else None
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
    if e.started_at is None and e.status == ExperimentStatus.preparing and e.prepared_at is not None:
        return (_as_utc(at) - _as_utc(e.prepared_at)).total_seconds() / 3600.0
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
        if isinstance(obj, Buffer):
            obj.molarity, obj.equivalents = body.molarity, body.equivalents
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
        if isinstance(obj, Buffer):
            if body.molarity is not None:
                obj.molarity = body.molarity or None
            if body.equivalents is not None:
                obj.equivalents = body.equivalents or None
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


# --- experiment templates (Library, 2.3) ------------------------------------------------------

TEMPLATE_KEYS = ("strain", "medium_id", "buffer_id", "buffer_volume_ml", "working_volume_ml", "profile_id",
                 "plan_params", "channels", "notes", "rules")


def _clean_template(d: Optional[dict], db: Session) -> dict:
    d = {k: v for k, v in (d or {}).items() if k in TEMPLATE_KEYS}
    if "channels" in d:
        d["channels"] = _check_channels(d["channels"])
    if "plan_params" in d:
        d["plan_params"] = _clean_plan(d["plan_params"])
    for k, model in (("medium_id", Medium), ("buffer_id", Buffer), ("profile_id", ControllerProfile)):
        if d.get(k) is not None and db.get(model, d[k]) is None:
            raise HTTPException(422, f"{k.replace('_id', '')} not found")
    d["rules"] = []   # FM2 rules come with the pump commands (verified on the controller first)
    return d


def _template_out(t: ExperimentTemplate, db: Session) -> dict:
    d = t.data or {}
    prof = db.get(ControllerProfile, d["profile_id"]) if d.get("profile_id") else None
    return {"id": t.id, "name": t.name, "description": t.description, "data": d, "archived": t.archived,
            "author": t.author, "updated_at": t.updated_at,
            "profile_name": prof.name if prof else None,
            "medium_name": (db.get(Medium, d["medium_id"]).name if d.get("medium_id") and db.get(Medium, d["medium_id"]) else None),
            "buffer_name": (db.get(Buffer, d["buffer_id"]).name if d.get("buffer_id") and db.get(Buffer, d["buffer_id"]) else None)}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), include_archived: bool = False):
    q = select(ExperimentTemplate).order_by(ExperimentTemplate.name)
    if not include_archived:
        q = q.where(ExperimentTemplate.archived.is_(False))
    return [_template_out(t, db) for t in db.scalars(q)]


@router.post("/templates", status_code=201)
def create_template(body: S.TemplateIn, request: Request, db: Session = Depends(get_db)):
    name = body.name.strip()
    if db.scalar(select(ExperimentTemplate).where(func.lower(ExperimentTemplate.name) == name.lower())):
        raise HTTPException(409, f"A template named {name} already exists.")
    if body.from_experiment_id is not None:
        e = _404(db.get(Experiment, body.from_experiment_id))
        data = {k: getattr(e, k) for k in TEMPLATE_KEYS if k != "rules" and hasattr(e, k)}
        data["plan_params"] = e.plan_params or (profile_params(e.profile) if e.profile else None)
        data["channels"] = e.channels
    else:
        data = body.data or {}
    t = ExperimentTemplate(name=name, description=body.description, data=_clean_template(data, db), author=who(request, db))
    db.add(t)
    db.flush()
    return _template_out(t, db)


@router.put("/templates/{tid}")
def update_template(tid: int, body: S.TemplatePatch, db: Session = Depends(get_db)):
    t = _404(db.get(ExperimentTemplate, tid))
    if body.name is not None:
        name = body.name.strip()
        if not name or db.scalar(select(ExperimentTemplate).where(func.lower(ExperimentTemplate.name) == name.lower(), ExperimentTemplate.id != tid)):
            raise HTTPException(409, "Name empty or already used.")
        t.name = name
    if body.description is not None:
        t.description = body.description
    if body.data is not None:
        t.data = _clean_template(body.data, db)
    if body.archived is not None:
        t.archived = body.archived
    db.flush()
    return _template_out(t, db)


@router.delete("/templates/{tid}", status_code=204)
def delete_template(tid: int, db: Session = Depends(get_db)):
    db.delete(_404(db.get(ExperimentTemplate, tid)))


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
    sess = object_session(p)
    if sess is not None:
        out.n_experiments = sess.scalar(select(func.count(Experiment.id)).where(Experiment.profile_id == p.id)) or 0
    return out


def _profile_data(body: S.ProfileIn) -> dict:
    data = body.model_dump()
    data["name"] = data["name"].strip()
    if data["ph_control_mode"] not in (None, "", "pid", "adaptive"):
        raise HTTPException(422, "pH control mode must be pid or adaptive")
    data["ph_control_mode"] = PhControlMode(data["ph_control_mode"]) if data["ph_control_mode"] else None
    if data["temperature_control_mode"] not in (None, "", "pid", "adaptive"):
        raise HTTPException(422, "temperature control mode must be pid or adaptive")
    data["temperature_control_mode"] = data["temperature_control_mode"] or None
    data["loop_assignments"] = _clean_assignments(data["loop_assignments"])
    data["channel_params"] = _clean_channel_params(data.get("channel_params"))
    for a, b in (("ph_low_limit", "ph_high_limit"), ("temperature_low_limit", "temperature_high_limit")):
        if data[a] is not None and data[b] is not None and data[a] >= data[b]:
            raise HTTPException(422, f"{a.replace('_', ' ')} must be lower than {b.replace('_', ' ')}")
    return data


def _name_free(db: Session, name: str, profile_id: Optional[int] = None) -> None:
    other = db.scalar(select(ControllerProfile).where(ControllerProfile.name == name))
    if other is not None and other.id != profile_id:
        raise HTTPException(409, f"A profile named '{name}' already exists")


@router.post("/profiles", response_model=S.ProfileOut, status_code=201)
def create_profile(body: S.ProfileIn, request: Request, db: Session = Depends(get_db)):
    data = _profile_data(body)
    data["author"] = who(request, db)
    _name_free(db, data["name"])
    p = ControllerProfile(**data)
    db.add(p)
    db.flush()
    return _profile_out(p)


@router.put("/profiles/{profile_id}", response_model=S.ProfileOut)
def update_profile(profile_id: int, body: S.ProfileIn, db: Session = Depends(get_db)):
    """
    Edit a saved profile. Experiments using it keep their own values: before the change each one
    gets a copy of the plan (if it had none) and of the profile as it was, so they show
    "profile changed after this plan was saved". A rename alone changes nothing else.
    """
    p = _404(db.get(ControllerProfile, profile_id), "Profile not found")
    data = _profile_data(body)
    data.pop("author", None)
    _name_free(db, data["name"], p.id)
    before = profile_params(p)
    for e in db.scalars(select(Experiment).where(Experiment.profile_id == p.id)):
        if e.plan_params is None:
            e.plan_params = dict(before)
        if e.profile_ref is None:
            e.profile_ref = dict(before)
    for k, v in data.items():
        setattr(p, k, v)
    if not _same(before, profile_params(p)):
        p.updated_at = utcnow()
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
    data["plan_params"] = _clean_plan(data.get("plan_params"))
    prof = db.get(ControllerProfile, data["profile_id"]) if data.get("profile_id") else None
    if data.get("profile_id") and prof is None:
        raise HTTPException(422, "Profile not found")
    e = Experiment(code=_next_code(db, Experiment, "FM"), **data)
    e.profile_ref = profile_params(prof) if prof else None
    _freeze_buffer(db, e)
    db.add(e)
    db.flush()
    return _exp_out(e)


def _freeze_buffer(db: Session, e: Experiment) -> None:
    """Copy the buffer's concentration into the plan (later Library edits do not change the experiment)."""
    b = db.get(Buffer, e.buffer_id) if e.buffer_id else None
    e.buffer_molarity = b.molarity if b else None
    e.buffer_equivalents = (b.equivalents or 1.0) if b and b.molarity else None


def buffer_factor(e: Experiment) -> Optional[float]:
    """mmol per mL of dosed buffer (mol/L x equivalents), None without a planned concentration."""
    return e.buffer_molarity * (e.buffer_equivalents or 1.0) if e.buffer_molarity else None


@router.get("/experiments/{exp_id}", response_model=S.ExperimentOut)
def get_experiment(exp_id: int, db: Session = Depends(get_db)):
    return _exp_out(_404(db.get(Experiment, exp_id)))


@router.patch("/experiments/{exp_id}", response_model=S.ExperimentOut)
def patch_experiment(exp_id: int, body: S.ExperimentPatch, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    data = body.model_dump(exclude_unset=True)
    if "channels" in data:
        data["channels"] = _check_channels(data["channels"])
    if "plan_params" in data:
        data["plan_params"] = _clean_plan(data["plan_params"])
    if e.status != ExperimentStatus.planned and "reactor_id" in data:
        raise HTTPException(409, "The reactor can only be changed while planned")
    if e.status not in (ExperimentStatus.planned, ExperimentStatus.preparing) and {"profile_id", "plan_params"} & data.keys():
        raise HTTPException(409, "Profile and controller parameters can only be changed before Start")
    new_profile = "profile_id" in data and data["profile_id"] != e.profile_id
    new_plan = "plan_params" in data and not _same(data["plan_params"], e.plan_params)
    if "buffer_volume_ml" in data:
        data["buffer_volume_ml"] = data["buffer_volume_ml"] or None
    new_buffer = "buffer_id" in data and data["buffer_id"] != e.buffer_id
    if new_buffer and e.status not in (ExperimentStatus.planned, ExperimentStatus.preparing):
        raise HTTPException(409, "The buffer can only be changed before Start")
    for k, v in data.items():
        setattr(e, k, v)
    if new_buffer or (e.buffer_id and e.buffer_molarity is None and e.status in (ExperimentStatus.planned, ExperimentStatus.preparing)):
        _freeze_buffer(db, e)
    if new_profile or new_plan:
        # The plan was (re)saved against the profile as it is now
        db.flush()
        db.refresh(e)
        e.profile_ref = profile_params(e.profile) if e.profile else None
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
    """Before Start: write the plan's setpoints, limits and pH control (mode + PID) to the controller."""
    e = _404(db.get(Experiment, exp_id))
    if e.status not in (ExperimentStatus.planned, ExperimentStatus.preparing):
        raise HTTPException(409, "The plan can only be loaded before Start")
    plan = plan_of(e)
    if not plan:
        raise HTTPException(404, "No profile or controller parameters in the plan")
    prof = SimpleNamespace(**plan)
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
    wanted = [loop for loop in LOOP_PLAN if getattr(prof, LOOP_PLAN[loop][0]) and loop in chans]
    diffs: dict[str, str] = {}
    if wanted and prof.loop_assignments:
        try:
            diffs = loops.assignment_differences(prof.loop_assignments, driver.read_loop_config())
        except DriverError as ex:
            diffs = {loop: f"cannot read the controller's assignments ({ex})" for loop in prof.loop_assignments}
    for loop in wanted:
        mode_key, pid_key = LOOP_PLAN[loop]
        if loop in diffs:   # parameters tuned for other actuators: not loaded
            results.append({"parameter": f"{loop}_control", "status": "skipped",
                            "message": f"actuators differ ({diffs[loop]}): control type and loop parameters not loaded"})
            continue
        try:
            commands.load_loop_control(driver, loop, PhControl(getattr(prof, mode_key), getattr(prof, pid_key)),
                                       e.status.value)
            results.append({"parameter": f"{loop}_control", "status": "confirmed"})
        except (DriverError, commands.CommandRejected) as ex:
            results.append({"parameter": f"{loop}_control", "status": "error", "message": str(ex)})
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
    plan = plan_of(e)
    p = SimpleNamespace(**plan) if plan else None
    chans = channels_of(e)
    if p:
        for param, planned in (("ph", p.ph_setpoint), ("temperature", p.temperature_setpoint),
                               ("stirrer", p.stirrer_setpoint)):
            if param not in chans:
                continue
            ctrl = snap.setpoint(param)
            rows.append({"parameter": f"{param}_setpoint", "plan": planned, "controller": ctrl,
                         "match": planned is None or (ctrl is not None and abs(ctrl - planned) < 1e-6)})
        for loop, (mode_key, pid_key) in LOOP_PLAN.items():
            mode = getattr(p, mode_key, None)
            if not mode or loop not in chans:
                continue
            ctl = snap.control(loop)
            rows.append({"parameter": mode_key, "plan": mode, "controller": ctl.mode, "match": mode == ctl.mode})
            pid = getattr(p, pid_key, None)
            param_diffs = [d for d in commands.ph_control_differences(PhControl(mode, pid), ctl)
                           if not d.startswith("mode ")]
            applicable = {k: v for k, v in (pid or {}).items()
                          if v is not None and k in commands.PH_MODE_PARAMS[mode]
                          and not (k == "deadband" and ctl.deadband_enabled is False)}
            if applicable:
                rows.append({"parameter": pid_key, "plan": applicable,
                             "controller": {k: (ctl.pid or {}).get(k) for k in applicable},
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
                                              Experiment.status.in_(ACTIVE), Experiment.id != e.id))
    assignment_diffs: dict[str, str] = {}
    if p and getattr(p, "loop_assignments", None):
        try:
            cfg = _loop_config(request, e.reactor_id)
            assignment_diffs = {k: m for k, m in loops.assignment_differences(p.loop_assignments, cfg).items()
                                if k in chans}
        except DriverError:
            pass
    return {"reachable": True, "reactor_free": busy is None, "rows": rows, "assignment_differences": assignment_diffs}


_LOOP_CACHE_S = 20.0


def _loop_config(request: Request, reactor_id: str, fresh: bool = False):
    """Loop configuration of a reactor, cached for a few seconds (it changes only on the display)."""
    cache = request.app.state.__dict__.setdefault("loop_config_cache", {})
    now = now_of(request)
    hit = cache.get(reactor_id)
    if hit and not fresh and (now - hit[0]).total_seconds() < _LOOP_CACHE_S:
        return hit[1]
    acq: Acquisition = request.app.state.acq
    if reactor_id not in acq.drivers:
        raise HTTPException(404, f"Unknown reactor {reactor_id}")
    cfg = acq.drivers[reactor_id].read_loop_config()
    cache[reactor_id] = (now, cfg)
    return cfg


@router.get("/reactors/{reactor_id}/loop-config")
def loop_config(reactor_id: str, request: Request, fresh: bool = False):
    """Loops, assigned actuators, cascades and calibration as set on the controller (read only)."""
    try:
        cfg = _loop_config(request, reactor_id, fresh)
    except DriverError as ex:
        return {"reachable": False, "message": str(ex), "loops": [], "unassigned_pumps": [], "assignments": {}}
    return {"reachable": True, **loops.config_view(cfg), "assignments": loops.assignments_of(cfg)}


ACTIVE = (ExperimentStatus.preparing, ExperimentStatus.fermenting)


def _busy(db: Session, e: Experiment) -> Optional[Experiment]:
    return db.scalar(select(Experiment).where(Experiment.reactor_id == e.reactor_id,
                                              Experiment.status.in_(ACTIVE), Experiment.id != e.id))


@router.post("/experiments/{exp_id}/prepare", response_model=S.ExperimentOut)
def prepare_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    """
    "Go to fermentation": the live page opens and the reactor is monitored with alerts, but nothing is
    recorded until Start (t = 0). Events of this phase get negative times at Start.
    """
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.planned:
        raise HTTPException(409, "Only planned experiments can go to fermentation")
    busy = _busy(db, e)
    if busy:
        raise HTTPException(409, f"{e.reactor_id} is in use by {busy.code}")
    e.status, e.prepared_at = ExperimentStatus.preparing, now_of(request)
    _event(db, e, "prepared", f"{e.code} moved to fermentation by {who(request, db)} (monitoring, not recording yet)", 0.0)
    db.flush()
    db.commit()
    request.app.state.acq.poll_once()
    db.refresh(e)
    return _exp_out(e)


@router.post("/experiments/{exp_id}/unprepare", response_model=S.ExperimentOut)
def unprepare_experiment(exp_id: int, request: Request, db: Session = Depends(get_db)):
    """Back to planning (nothing was recorded; events are kept)."""
    e = _404(db.get(Experiment, exp_id))
    if e.status != ExperimentStatus.preparing:
        raise HTTPException(409, "Only an experiment in preparation can go back to planning")
    db.execute(sa_delete(ControllerReading).where(ControllerReading.experiment_id == e.id))
    e.status, e.prepared_at = ExperimentStatus.planned, None
    request.app.state.acq.alert_states.pop((e.id, "pre"), None)
    _event(db, e, "unprepared", f"{e.code} back to planning by {who(request, db)}")
    return _exp_out(e)


@router.post("/experiments/{exp_id}/start", response_model=S.ExperimentOut)
def start_experiment(exp_id: int, request: Request, body: Optional[S.StartIn] = None, db: Session = Depends(get_db)):
    """Start of the run (t = 0), from planning or from the preparation phase."""
    e = _404(db.get(Experiment, exp_id))
    checks = (body.checklist if body else None) or []
    if e.status not in (ExperimentStatus.planned, ExperimentStatus.preparing):
        raise HTTPException(409, "Only planned or preparing experiments can be started")
    busy = _busy(db, e)
    if busy:
        raise HTTPException(409, f"{e.reactor_id} is in use by {busy.code}")
    acq: Acquisition = request.app.state.acq
    driver = acq.drivers[e.reactor_id]
    if hasattr(driver, "on_experiment_start"):
        driver.on_experiment_start()  # simulator only: fresh culture
    now = now_of(request)
    if e.status == ExperimentStatus.preparing and e.prepared_at is not None:
        # pre-start readings and events: hours before Start (negative)
        shift = (_as_utc(now) - _as_utc(e.prepared_at)).total_seconds() / 3600.0
        for r in db.scalars(select(ControllerReading).where(ControllerReading.experiment_id == e.id)):
            r.t_h = r.t_h - shift
        for ev in db.scalars(select(Event).where(Event.experiment_id == e.id, Event.t_h.is_not(None))):
            ev.t_h = ev.t_h - shift
        acq.alert_states.pop((e.id, "pre"), None)
    e.status, e.started_at = ExperimentStatus.fermenting, now
    _event(db, e, "started", f"{e.code} started by {who(request, db)}"
           + (f" · pre-start checks confirmed: {'; '.join(checks)}" if checks else ""), 0.0)
    plan = plan_of(e)
    if plan:
        e.plan_snapshot = {"profile": e.profile.name if e.profile else None,
                           "custom": bool(e.plan_params and e.profile and not _same(e.plan_params, profile_params(e.profile))),
                           **{k: v for k, v in plan.items() if not k.endswith("_limit")},
                           "ph_limits": [plan.get("ph_low_limit"), plan.get("ph_high_limit")],
                           "temperature_limits": [plan.get("temperature_low_limit"), plan.get("temperature_high_limit")]}
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
    cfgb = request.app.state.app_settings.get("backup")
    want = body.save_copy if body.save_copy is not None else (cfgb.get("archive_on_end") and bool(cfgb.get("archive_destination")))
    if want:
        try:
            out.copy = save_experiment_copy(db, request, exp_id)
            db.commit()
        except HTTPException as ex:   # the run is ended anyway; report why the copy was not saved
            out.copy = {"error": ex.detail}
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
    """
    Controller readings, one column per field; optional-channel values become columns too.
    The dose counters (base_, acid_, antifoam_cumulative_ml) are continued across counter resets
    (stitch_counter): they are the total dosed since Start, used by the analysis. When a reset
    happened, the raw counter is kept as <x>_counter_raw_ml. The whole series is read so that the
    offset is right also for since_h > 0. Resets made with FM2 are shown from 0 by get_readings.
    """
    rows = db.execute(select(ControllerReading).where(ControllerReading.experiment_id == exp_id)
                      .order_by(ControllerReading.t_h)).scalars().all()
    cols = READING_FIELDS + list(EXTRA_FIELDS)
    df = pd.DataFrame([{f: getattr(r, f) for f in READING_FIELDS} | {k: (r.extra or {}).get(k) for k in EXTRA_FIELDS}
                       | {"ts": r.ts} for r in rows])
    if df.empty:
        return pd.DataFrame(columns=cols + ["ts"])
    for col in DOSE_COUNTERS.values():
        if col not in df:
            continue
        raw = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
        if base_dosing.count_resets(raw):
            df[col.replace("_cumulative_ml", "_counter_raw_ml")] = raw
            df[col] = base_dosing.stitch_counter(raw)
    if since_h > 0:
        df = df[df["t_h"] >= since_h].reset_index(drop=True)
    return df


def _manual_resets(db: Session, exp_id: int) -> dict[str, list[float]]:
    """{counter: [t_h, …]} of the counter resets made with FM2 during the experiment."""
    out: dict[str, list[float]] = {}
    for ev in db.scalars(select(Event).where(Event.experiment_id == exp_id,
                                             Event.kind.like(f"{MANUAL_RESET_KIND}:%"),
                                             Event.severity == "info")):
        if ev.t_h is not None:
            out.setdefault(ev.kind.split(":", 1)[1], []).append(ev.t_h)
    return out


def _restart_at_manual_resets(db: Session, exp_id: int, df: pd.DataFrame) -> pd.DataFrame:
    """
    For the charts: a counter reset with FM2 shows as a restart from 0 (as on the controller), while
    a reset FM2 did not make (power cycle, display) stays continued. The complete totals are kept as
    <x>_total_ml when a manual reset happened.
    """
    resets = _manual_resets(db, exp_id)
    if df.empty or not resets:
        return df
    t = df["t_h"].to_numpy(dtype=float)
    for counter, times in resets.items():
        col = DOSE_COUNTERS.get(counter)
        if col not in df:
            continue
        total = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
        shown = total.copy()
        for tr in sorted(times):
            before = np.where((t < tr) & np.isfinite(total))[0]
            base = total[before[-1]] if len(before) else 0.0
            after = t >= tr
            shown[after] = total[after] - base
        df[col.replace("_cumulative_ml", "_total_ml")] = total
        df[col] = np.maximum(shown, 0.0)
    return df


@router.get("/experiments/{exp_id}/readings")
def get_readings(exp_id: int, db: Session = Depends(get_db), since_h: float = 0.0, max_points: int = 2000):
    _404(db.get(Experiment, exp_id))
    df = _restart_at_manual_resets(db, exp_id, _readings_df(db, exp_id))
    if since_h > 0:
        df = df[df["t_h"] >= since_h].reset_index(drop=True)
    if len(df) > max_points:  # decimate for the browser; full data stays in the database/exports
        df = df.iloc[np.linspace(0, len(df) - 1, max_points).astype(int)]
    df = df.drop(columns=["ts"]).replace({np.nan: None})
    return {"columns": list(df.columns), "rows": df.values.tolist()}


def _meas_out(m: ManualMeasurement) -> S.MeasurementOut:
    return S.MeasurementOut(id=m.id, experiment_id=m.experiment_id, variable_id=m.variable_id,
                            variable_name=m.variable.name, sampled_at=m.sampled_at, t_h=m.t_h, value=m.value,
                            unit=m.unit, note=m.note, author=m.author, volume_taken_ml=m.volume_taken_ml)


@router.get("/experiments/{exp_id}/measurements", response_model=list[S.MeasurementOut])
def list_measurements(exp_id: int, db: Session = Depends(get_db)):
    _404(db.get(Experiment, exp_id))
    q = select(ManualMeasurement).where(ManualMeasurement.experiment_id == exp_id).order_by(ManualMeasurement.t_h)
    return [_meas_out(m) for m in db.scalars(q)]


@router.post("/experiments/{exp_id}/measurements", response_model=S.MeasurementOut, status_code=201)
def add_measurement(exp_id: int, body: S.MeasurementIn, request: Request, db: Session = Depends(get_db)):
    e = _404(db.get(Experiment, exp_id))
    var = _404(db.get(Variable, body.variable_id), "Unknown variable")
    # any status once started: e.g. riboflavin measured days later on frozen aliquots of a completed run
    if body.t_h is not None:
        if body.sampled_at is not None:
            raise HTTPException(422, "Give either the sampling time or t (h), not both")
        if e.started_at is None:
            raise HTTPException(409, "Experiment not started: t (h) is counted from Start")
        if body.t_h < 0:
            raise HTTPException(422, "t (h) must be ≥ 0")
        at = _as_utc(e.started_at) + timedelta(hours=body.t_h)
    else:
        at = body.sampled_at or now_of(request)
    m = ManualMeasurement(experiment_id=e.id, variable_id=var.id, sampled_at=at, t_h=_t_h(e, at),
                          value=body.value, unit=body.unit or var.unit, note=body.note, author=who(request, db),
                          volume_taken_ml=body.volume_taken_ml)
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
    names = {r.id: r for r in db.scalars(select(Reactor))}
    order = sorted(acq.live.items(), key=lambda kv: (names[kv[0]].position if kv[0] in names else 99, kv[0]))
    for rid, live in order:
        exp = db.scalar(select(Experiment).where(Experiment.reactor_id == rid, Experiment.status.in_(ACTIVE)))
        snap = live.snapshot
        out.append({
            "reactor_id": rid,
            "name": names[rid].name if rid in names else rid,
            "interval_s": acq.interval_of(rid),
            "online": live.last_error is None and live.last_ok is not None,
            "last_reading_age_s": (now - live.last_ok).total_seconds() if live.last_ok else None,
            "error": live.last_error,
            "snapshot": None if snap is None else {k: v for k, v in snap.__dict__.items()
                                                   if k not in ("ts", "ph_control", "temperature_control")},
            "ph_control": None if snap is None else snap.ph_control.__dict__,
            "temperature_control": None if snap is None else snap.temperature_control.__dict__,
            "experiment": None if exp is None else {"id": exp.id, "code": exp.code, "name": exp.name, "strain": exp.strain,
                                                    "status": exp.status.value,
                                                    "t_h": _t_h(exp, now), "channels": channels_of(exp),
                                                    "parallel_run": exp.parallel_run.code if exp.parallel_run else None},
        })
    return out


# --- bioreactors & probes (Settings, 2.3) ----------------------------------------------------

def _reactor_out(r: Reactor, acq: Acquisition, db: Session, now: datetime) -> dict:
    live = acq.live.get(r.id)
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == r.id, Experiment.status.in_(ACTIVE)))
    used = db.scalar(select(func.count()).select_from(Experiment).where(Experiment.reactor_id == r.id))
    status = ("disabled" if not r.enabled else "simulated" if r.driver == "simulator" and live and live.last_ok
              else "connected" if live and live.last_ok and live.last_error is None
              else "not_reachable" if live and live.last_error else "starting")
    return {"id": r.id, "name": r.name, "driver": r.driver, "driver_label": DRIVERS.get(r.driver, r.driver),
            "address": r.address, "interval_s": r.interval_s, "enabled": r.enabled, "position": r.position,
            "status": status, "error": live.last_error if live else None,
            "last_reading_age_s": (now - live.last_ok).total_seconds() if live and live.last_ok else None,
            "active_experiment": exp.code if exp else None, "experiments": used,
            "interval_warning": r.interval_s < INTERVAL_WARN_S or r.interval_s > 60}


@router.get("/bioreactors")
def list_bioreactors(request: Request, db: Session = Depends(get_db)):
    acq: Acquisition = request.app.state.acq
    now = now_of(request)
    return {"reactors": [_reactor_out(r, acq, db, now) for r in db.scalars(select(Reactor).order_by(Reactor.position, Reactor.id))],
            "drivers": [{"id": k, "label": v} for k, v in DRIVERS.items()],
            "interval": {"min": INTERVAL_MIN_S, "max": INTERVAL_MAX_S, "warn_below": INTERVAL_WARN_S, "default": request.app.state.settings.sampling_interval_s}}


def _check_reactor_fields(driver: Optional[str], address: Optional[str], interval_s: Optional[float]) -> None:
    if driver is not None and driver not in DRIVERS:
        raise HTTPException(422, f"Driver must be one of: {', '.join(DRIVERS.values())}.")
    if interval_s is not None and not (INTERVAL_MIN_S <= interval_s <= INTERVAL_MAX_S):
        raise HTTPException(422, f"Sampling interval must be {INTERVAL_MIN_S:g}-{INTERVAL_MAX_S:g} s.")
    if address is not None and len(address) > 200:
        raise HTTPException(422, "Address too long.")


def _apply_reactor(request: Request, r: Reactor) -> None:
    """Make the running acquisition match the saved configuration of one bioreactor."""
    acq: Acquisition = request.app.state.acq
    if not r.enabled:
        acq.remove_reactor(r.id)
        return
    if r.driver != "simulator" and not r.address.strip():
        acq.remove_reactor(r.id)
        return
    clock = request.app.state.clock
    acq.set_reactor(r.id, make_driver(r.driver, r.id, r.address.strip(), clock=None if clock is _utcnow else clock), r.interval_s)


@router.post("/bioreactors", status_code=201)
def create_bioreactor(body: S.ReactorIn, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    _check_reactor_fields(body.driver, body.address, body.interval_s)
    n = 1
    while db.get(Reactor, f"R{n}"):
        n += 1
    rid = f"R{n}"
    name = (body.name or f"Bioreactor {n}").strip()[:60]
    if db.scalar(select(Reactor).where(func.lower(Reactor.name) == name.lower())):
        raise HTTPException(409, f"A bioreactor named {name} already exists.")
    pos = (db.scalar(select(func.max(Reactor.position))) or 0) + 1
    r = Reactor(id=rid, name=name, driver=body.driver or "simulator", address=(body.address or "").strip(),
                interval_s=body.interval_s or request.app.state.settings.sampling_interval_s,
                enabled=bool(body.enabled), position=pos)
    db.add(r)
    db.flush()
    _apply_reactor(request, r)
    return _reactor_out(r, request.app.state.acq, db, now_of(request))


@router.patch("/bioreactors/{rid}")
def update_bioreactor(rid: str, body: S.ReactorPatch, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    r = _404(db.get(Reactor, rid), "Unknown bioreactor")
    _check_reactor_fields(body.driver, body.address, body.interval_s)
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == rid, Experiment.status.in_(ACTIVE)))
    reconnect = ((body.driver is not None and body.driver != r.driver)
                 or (body.address is not None and body.address.strip() != r.address)
                 or (body.enabled is not None and body.enabled != r.enabled))
    if exp and reconnect and not body.confirm:
        raise HTTPException(409, f"{exp.code} is running on {r.name}: recording pauses while the controller reconnects. Confirm to go on.")
    if body.name is not None:
        name = body.name.strip()[:60]
        if not name or db.scalar(select(Reactor).where(func.lower(Reactor.name) == name.lower(), Reactor.id != rid)):
            raise HTTPException(409, "Name empty or already used.")
        r.name = name
    if body.driver is not None:
        r.driver = body.driver
    if body.address is not None:
        r.address = body.address.strip()
    if body.enabled is not None:
        r.enabled = body.enabled
    if body.interval_s is not None:
        r.interval_s = body.interval_s
    db.flush()
    if reconnect:
        _apply_reactor(request, r)
    elif body.interval_s is not None and rid in request.app.state.acq.drivers:
        request.app.state.acq.set_interval(rid, r.interval_s)
    if exp and (reconnect or body.interval_s is not None):
        _event(db, exp, "bioreactor_config", f"{r.name}: configuration changed by {who(request, db)}"
               + (f" (sampling every {r.interval_s:g} s)" if body.interval_s is not None else ""))
    return _reactor_out(r, request.app.state.acq, db, now_of(request))


@router.delete("/bioreactors/{rid}", status_code=204)
def delete_bioreactor(rid: str, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    r = _404(db.get(Reactor, rid), "Unknown bioreactor")
    if db.scalar(select(func.count()).select_from(Experiment).where(Experiment.reactor_id == rid)):
        raise HTTPException(409, f"{r.name} is used by experiments: disable it instead.")
    request.app.state.acq.remove_reactor(rid)
    db.delete(r)


@router.post("/bioreactors/{rid}/test")
def test_bioreactor(rid: str, body: S.ReactorTestIn, request: Request, db: Session = Depends(get_db)):
    """Read the controller once with the given (or saved) driver and address; nothing is written."""
    r = _404(db.get(Reactor, rid), "Unknown bioreactor")
    driver_kind = body.driver or r.driver
    address = (body.address if body.address is not None else r.address).strip()
    _check_reactor_fields(driver_kind, address, None)
    if driver_kind != "simulator" and not address:
        return {"ok": False, "error": "Give the controller's address."}
    acq: Acquisition = request.app.state.acq
    same = driver_kind == r.driver and address == r.address and rid in acq.drivers
    try:
        drv = acq.drivers[rid] if same else make_driver(driver_kind, rid, address)
        snap = drv.read()
    except (DriverError, ValueError) as ex:
        return {"ok": False, "error": str(ex)}
    finally:
        if not same:
            try:
                drv.close()   # type: ignore[possibly-undefined]
            except Exception:
                pass
    vals = {k: v for k, v in snap.__dict__.items() if isinstance(v, (int, float)) and v is not None}
    found = [c for c in ("ph", "temperature", "stirrer", "do2", "redox") if vals.get(c) is not None or (snap.extra or {}).get(c) is not None]
    return {"ok": True, "channels": found, "values": {k: vals[k] for k in ("ph", "temperature", "stirrer") if k in vals}}


@router.get("/reactors/{reactor_id}/history")
def reactor_history(reactor_id: str, request: Request):
    """Live values of the last 3 hours, kept in memory only (Trends of a bioreactor without experiment)."""
    acq: Acquisition = request.app.state.acq
    _404(acq.drivers.get(reactor_id), "Unknown reactor")
    return {"interval_s": acq.interval_of(reactor_id), "window_h": 3, "points": acq.history_of(reactor_id)}


@router.post("/reactors/{reactor_id}/setpoints")
def change_setpoint(reactor_id: str, body: S.SetpointIn, request: Request, db: Session = Depends(get_db)):
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id, Experiment.status.in_(ACTIVE)))
    alert_state = acq.alert_state_for(exp.id, reactor_id, exp.status == ExperimentStatus.preparing) if exp else None
    _check_remote(request)
    try:
        res = commands.write_setpoint(driver, body.parameter, body.value,
                                      request.app.state.app_settings.setpoint_ranges(), alert_state)
    except commands.CommandRejected as ex:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id,
                               parameter=body.parameter, requested_value=body.value, status="rejected",
                               message=str(ex), origin=origin_of(request), author=who(request, db)))
        db.commit()
        raise HTTPException(422, str(ex))
    db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=res.parameter,
                           old_value=res.old_value, requested_value=res.requested_value,
                           readback_value=res.readback_value, status=res.status, message=res.message,
                           origin=origin_of(request), author=who(request, db)))
    return res.__dict__


@router.get("/reactors/{reactor_id}/loops")
def loops_state(reactor_id: str, request: Request):
    """What the Start/Stop selected loops dialog needs: loops, their state, and whether FM2 may switch them."""
    acq: Acquisition = request.app.state.acq
    _404(acq.drivers.get(reactor_id), "Unknown reactor")
    allowed = request.app.state.app_settings.allowed_loops(reactor_id)
    try:
        cfg = _loop_config(request, reactor_id, fresh=True)
    except DriverError as ex:
        return {"reachable": False, "message": str(ex), "allowed": allowed, "loops": []}
    view = loops.config_view(cfg)["loops"]
    out = []
    for lp in view:
        if lp["key"] not in SWITCHABLE_LOOPS:
            continue
        reason = _switch_refusal(lp, view, allowed, start=True)
        out.append({**lp, "allowed": lp["key"] in allowed, "start_refused": reason,
                    "also_switches": [o["label"] for o in view if o["drives"] == lp["key"]]})
    return {"reachable": True, "allowed": allowed, "loops": out}


def _switch_refusal(lp: dict, view: list[dict], allowed: list[str], start: bool) -> Optional[str]:
    if lp["key"] not in allowed:
        return "not enabled in Settings › Loop control"
    if not lp["installed"] or lp["available"] < 4:
        return "not set up as a control loop on the controller"
    if start and not lp["actuators"]:
        return "no actuator assigned on the controller"
    if start:
        for o in view:   # loops the controller starts with it (cascade) must be allowed too
            if o["drives"] == lp["key"] and o["key"] not in allowed:
                return f"it would also start the {o['label']} loop, which is not enabled in Settings"
    return None


@router.post("/reactors/{reactor_id}/loops")
def switch_loops(reactor_id: str, body: S.LoopSwitchIn, request: Request, db: Session = Depends(get_db)):
    """Start or stop the selected control loops (allowed ones only), with read-back and an event."""
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    _check_remote(request)
    if not is_local(request) and not request.app.state.app_settings.get("loops")["allow_remote"]:
        raise HTTPException(403, "Starting and stopping loops from remote devices is switched off (Settings › Loop control).")
    if not body.loops:
        raise HTTPException(422, "No loop selected")
    allowed = request.app.state.app_settings.allowed_loops(reactor_id)
    try:
        cfg = _loop_config(request, reactor_id, fresh=True)
    except DriverError as ex:
        raise HTTPException(503, f"{reactor_id} not reachable: {ex}")
    view = {lp["key"]: lp for lp in loops.config_view(cfg)["loops"]}
    for key in body.loops:
        if key not in view or key not in SWITCHABLE_LOOPS:
            raise HTTPException(422, f"Unknown loop {key!r}")
        why = _switch_refusal(view[key], list(view.values()), allowed, start=body.on)
        if why:
            raise HTTPException(409, f"{view[key]['label']}: {why}. Nothing was switched.")
    cascades = {k: [o["key"] for o in view.values() if o["drives"] == k] for k in body.loops}
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id, Experiment.status.in_(ACTIVE)))
    alert_state = acq.alert_state_for(exp.id, reactor_id, exp.status == ExperimentStatus.preparing) if exp else None
    results = commands.switch_loops(driver, list(body.loops), body.on, cascades, alert_state)
    request.app.state.__dict__.get("loop_config_cache", {}).pop(reactor_id, None)
    author = who(request, db)
    for r in results:
        label = view[r.loop]["label"]
        extra = cascades.get(r.loop)
        msg = (f"{reactor_id}: {label} loop {'started' if body.on else 'stopped'} by {author}"
               + (f" (with {', '.join(view[x]['label'] for x in extra)} in cascade)" if extra else "")
               + ("" if r.status == "confirmed" else f" — NOT confirmed: {r.message}"))
        _reactor_event(db, request, reactor_id, exp, "loop_started" if body.on else "loop_stopped", msg,
                       severity="info" if r.status == "confirmed" else "alert")
    return {"results": [r.__dict__ for r in results]}


# --- dose counters ----------------------------------------------------------------------
MANUAL_RESET_KIND = "counter_reset_manual"     # kind "counter_reset_manual:<counter>"


@router.get("/reactors/{reactor_id}/counters")
def counters_state(reactor_id: str, request: Request):
    """Dose counters FM2 can reset, with their current value (None = not reported by the controller)."""
    acq: Acquisition = request.app.state.acq
    _404(acq.drivers.get(reactor_id), "Unknown reactor")
    snap = acq.live[reactor_id].snapshot
    return {"reachable": snap is not None,
            "counters": [{"key": k, "label": DOSE_COUNTER_LABELS[k],
                          "value": None if snap is None else counter_value(snap, k)} for k in DOSE_COUNTERS]}


@router.post("/reactors/{reactor_id}/counters/reset")
def reset_counters(reactor_id: str, body: S.CounterResetIn, request: Request, db: Session = Depends(get_db)):
    """Reset the selected dose counters to 0 (like "Reset Dose Monitor" on the controller), with read-back.
    During an experiment the cumulative curve restarts from 0 there; totals in the analysis stay complete."""
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    _check_remote(request)
    if not body.counters:
        raise HTTPException(422, "No counter selected")
    bad = [c for c in body.counters if c not in DOSE_COUNTERS]
    if bad:
        raise HTTPException(422, f"Unknown counter {bad[0]!r}")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id, Experiment.status.in_(ACTIVE)))
    alert_state = acq.alert_state_for(exp.id, reactor_id, exp.status == ExperimentStatus.preparing) if exp else None
    results = commands.reset_counters(driver, list(dict.fromkeys(body.counters)), alert_state)
    author = who(request, db)
    for r in results:
        label = DOSE_COUNTER_LABELS[r.counter]
        before = "—" if r.before is None else f"{r.before:.2f} mL"
        msg = (f"{reactor_id}: {label} counter reset by {author} (was {before})"
               + ("" if r.status == "confirmed" else f" — NOT confirmed: {r.message}"))
        _reactor_event(db, request, reactor_id, exp, f"{MANUAL_RESET_KIND}:{r.counter}", msg,
                       severity="info" if r.status == "confirmed" else "alert")
    return {"results": [r.__dict__ for r in results]}


@router.get("/reactors/{reactor_id}/activity")
def reactor_activity(reactor_id: str, request: Request, db: Session = Depends(get_db), hours: float = 72.0):
    """Commands and events of a reactor (with or without an experiment) in the last hours: monitoring page."""
    _404(request.app.state.acq.drivers.get(reactor_id), "Unknown reactor")
    since = now_of(request) - timedelta(hours=hours)
    ev = db.scalars(select(Event).where(Event.reactor_id == reactor_id, Event.at >= since)
                    .order_by(Event.at.desc()).limit(200)).all()
    cmds = db.scalars(select(SetpointCommand).where(SetpointCommand.reactor_id == reactor_id, SetpointCommand.at >= since)
                      .order_by(SetpointCommand.at.desc()).limit(200)).all()
    return {"events": [S.EventOut.model_validate(x).model_dump() for x in ev],
            "changes": [{"at": c.at, "parameter": c.parameter, "old": c.old_value, "new": c.requested_value,
                         "readback": c.readback_value, "status": c.status, "origin": c.origin, "author": c.author,
                         "experiment_id": c.experiment_id} for c in cmds]}


def is_local(request: Request) -> bool:
    """
    True for requests from the lab PC itself (loopback). Decided from the connection, never from
    what the browser declares. "testclient" is Starlette's test client (not a network address).
    """
    # Behind "tailscale serve" (HTTPS for the installable app) every request reaches us from
    # 127.0.0.1: the proxy marks them with X-Forwarded-For / Tailscale-User-* headers, and those
    # are remote. A local browser adding such a header only makes itself "remote" (the safe side).
    h = request.headers
    if h.get("x-forwarded-for") or h.get("tailscale-user-login") or h.get("forwarded"):
        return False
    host = request.client.host if request.client else ""
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def origin_of(request: Request) -> str:
    return "local" if is_local(request) else "remote"


LOCAL_ONLY_SECTIONS = ("remote", "backup", "loops")


def _check_remote(request: Request, _declared: str = "") -> None:
    if not is_local(request) and not request.app.state.app_settings.get("remote")["allow_remote_setpoints"]:
        raise HTTPException(403, "Changes from remote devices are switched off (Settings › Remote access).")


@router.post("/reactors/{reactor_id}/limits")
def change_limits(reactor_id: str, body: S.LimitsIn, request: Request, db: Session = Depends(get_db)):
    """Change the process (alarm) limits of pH or temperature, with read-back; logged per limit."""
    acq: Acquisition = request.app.state.acq
    driver = _404(acq.drivers.get(reactor_id), "Unknown reactor")
    exp = db.scalar(select(Experiment).where(Experiment.reactor_id == reactor_id, Experiment.status.in_(ACTIVE)))
    _check_remote(request)
    try:
        results = commands.write_limits(driver, body.parameter, body.low, body.high)
    except commands.CommandRejected as ex:
        raise HTTPException(422, str(ex))
    for r in results:
        db.add(SetpointCommand(experiment_id=exp.id if exp else None, reactor_id=reactor_id, parameter=r.parameter,
                               old_value=r.old_value, requested_value=r.requested_value,
                               readback_value=r.readback_value, status=r.status, message=r.message,
                               origin=origin_of(request), author=who(request, db)))
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


@router.get("/experiments/{exp_id}/timeline")
def experiment_timeline(exp_id: int, db: Session = Depends(get_db), limit: int = 500):
    """
    One list for the "Latest events" panel (2.3): alerts and detected events, setpoint and limit changes,
    loop switching and counter resets (the old change log), and samplings. Newest first.
    """
    e = _404(db.get(Experiment, exp_id))
    items: list[dict] = []
    for ev in db.scalars(select(Event).where(Event.experiment_id == exp_id)):
        kind = ("detected" if ev.kind in DETECTED_EVENT_KINDS else "alert" if ev.severity == "alert"
                else "loops" if ev.kind in ("loop", "loops", "counter_reset") else "info")
        items.append({"at": _as_utc(ev.at).isoformat(), "t_h": ev.t_h, "category": kind, "title": ev.message,
                      "detail": ev.kind.replace("_", " "), "severity": ev.severity, "author": None})
    for c in db.scalars(select(SetpointCommand).where(SetpointCommand.experiment_id == exp_id)):
        unit = {"ph": "", "temperature": " °C", "stirrer": " rpm"}.get(c.parameter, "")
        title = (f"{c.parameter.replace('_', ' ')} {c.old_value:g} → {c.requested_value:g}{unit}" if c.old_value is not None
                 else f"{c.parameter.replace('_', ' ')} → {c.requested_value:g}{unit}")
        items.append({"at": _as_utc(c.at).isoformat(), "t_h": _t_h(e, _as_utc(c.at)) if e.started_at else None,
                      "category": "change", "title": title,
                      "detail": f"{c.status} · {c.origin}" + (f" · {c.message}" if c.message else ""),
                      "severity": "alert" if c.status in ("rejected", "error", "mismatch") else "info", "author": c.author})
    for m in db.scalars(select(ManualMeasurement).where(ManualMeasurement.experiment_id == exp_id)):
        items.append({"at": _as_utc(m.sampled_at).isoformat(), "t_h": m.t_h, "category": "sampling",
                      "title": f"{m.variable.name} = {m.value:g}{(' ' + m.unit) if m.unit else ''}",
                      "detail": "manual" + (f" · {m.volume_taken_ml:g} mL taken" if m.volume_taken_ml else ""),
                      "severity": "info", "author": m.author})
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]


DETECTED_EVENT_KINDS = ("phase_acidification", "phase_exponential", "probably_finished", "finished")


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
        out["base_dosing"] = _with_mmol(bd.__dict__, buffer_factor(db.get(Experiment, exp_id)))
    else:
        out["base_dosing"] = None
    return out


def _with_mmol(bd: dict, factor: Optional[float]) -> dict:
    """Same quantities in mmol of buffer (mL x mol/L x equivalents); None without a planned concentration."""
    bd = dict(bd)
    bd["total_mmol"] = bd.get("total_ml") * factor if factor and bd.get("total_ml") is not None else None
    bd["peak_rate_mmol_min"] = bd.get("peak_rate_ml_min") * factor if factor and bd.get("peak_rate_ml_min") is not None else None
    bd["buffer_mmol_per_ml"] = factor
    return bd


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
def experiment_analysis(exp_id: int, request: Request, db: Session = Depends(get_db), fit_variable: str = "OD600",
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
    # Addition 2 (acid) on the same bins, when the controller reports its counter
    acid = pd.to_numeric(df["acid_cumulative_ml"], errors="coerce") if "acid_cumulative_ml" in df else None
    res["acid_rate_bins"] = [] if acid is None or not acid.notna().any() else [
        b.__dict__ for b in base_dosing.rate_bins(df["t_h"].astype(float), acid.astype(float), bin_h)]
    ch = channels_of(e)
    res["control"] = control.summarize(df, ch, plan_of(e)) if not df.empty else {"loops": [], "doses": {}}
    res["estimated_end"] = _estimated_end(request, df) if "ph" in ch else None
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
                    "out_of_limit_events": int(n_out),
                    "t_end_h": (res.get("estimated_end") or {}).get("t_end_h")},
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
                      fit: bool = True, mono: bool = False, request: Request = None, db: Session = Depends(get_db)):
    """Time-course figure of the selected panels (manual variables by name, controller series by key)."""
    e = _404(db.get(Experiment, exp_id))
    if fmt not in report.FIG_FORMATS:
        raise HTTPException(404, "Format not available")
    if not (40 <= width_mm <= 500) or not (72 <= dpi <= 1200):
        raise HTTPException(422, "Width must be 40-500 mm and resolution 72-1200 dpi")
    an = experiment_analysis(exp_id, request, db, model=model, step_h=step_h)
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
        data = report.render_figure(panels, fmt, width_mm, dpi, theme, mono=mono)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return StreamingResponse(io.BytesIO(data), media_type=report.FIG_FORMATS[fmt],
                             headers={"Content-Disposition": f'attachment; filename="{e.code}-time-course{"-bw" if mono else ""}.{fmt}"'})


SESSION_PARAMS = [  # (key, label, unit, source); "fit" keys come from the model fit on the session's variable
    ("lag_h", "Lag time λ", "h", "fit"),
    ("mu_max_per_h", "Maximum specific growth rate μmax", "h⁻¹", "fit"),
    ("doubling_time_h", "Doubling time at μmax", "h", "fit"),
    ("t_mu_max_h", "Time at μmax", "h", "fit"),
    ("X_max", "Predicted maximum X₀·e^A", "", "fit"),
    ("A_ln", "Asymptote A", "ln X/X₀", "fit"),
    ("total_ml", "Total base", "mL", "controller, cumulative at end"),
    ("total_mmol", "Total base", "mmol", "mL × planned concentration × equivalents"),
    ("peak_rate_ml_min", "Peak base rate", "mL min⁻¹", "15-min bins"),
    ("t_peak_h", "Time of peak base rate", "h", "15-min bins"),
    ("t_half_total_h", "Time to 50 % of total base", "h", "controller, cumulative"),
    ("last_hour_rate_ml_min", "Base rate in the last hour", "mL min⁻¹", "slowdown at end of run"),
    ("base_per_final_od", "Total base per final OD600", "mL AU⁻¹", "acid produced per biomass, proxy"),
    ("t_end_h", "Estimated end of fermentation", "h", "base rate below the 'probably finished' threshold"),
    # how well the loops held the setpoint (analysis.control): effective mean vs setpoint
    ("ph_mean", "pH, effective mean during regulation", "", "control"),
    ("ph_bias", "pH, mean deviation from setpoint", "", "control"),
    ("ph_within_tol_pct", "pH, time within ±0.05 of setpoint", "%", "control"),
    ("ph_dose_start", "pH when base dosing starts (median)", "", "control"),
    ("temperature_mean", "T, effective mean during regulation", "°C", "control"),
    ("temperature_bias", "T, mean deviation from setpoint", "°C", "control"),
    ("temperature_within_tol_pct", "T, time within ±0.3 °C of setpoint", "%", "control"),
]


def _end_params(request: Optional[Request]):
    return request.app.state.app_settings.alert_params() if request is not None else AlertParams()


def _estimated_end(request: Optional[Request], df: pd.DataFrame) -> Optional[dict]:
    """End of fermentation by the rule of the "probably finished" notification (same settings)."""
    if df.empty or "base_cumulative_ml" not in df:
        return None
    p = _end_params(request)
    return base_dosing.estimate_end(df["t_h"].astype(float), pd.to_numeric(df["base_cumulative_ml"], errors="coerce"),
                                    p.finished_fraction, p.finished_hold_s / 3600.0, p.rate_window_s / 3600.0).__dict__


def _control_values(ctrl: dict) -> dict:
    """Flat values of analysis.control.summarize() for SESSION_PARAMS and the export."""
    out = {}
    for lp in ctrl.get("loops", []):
        k = lp["loop"]
        out[f"{k}_mean"], out[f"{k}_bias"], out[f"{k}_within_tol_pct"] = lp["mean"], lp["bias"], lp["within_tol_pct"]
    out["ph_dose_start"] = (ctrl.get("doses", {}).get("base") or {}).get("ph_start_median")
    return out
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


def _experiment_values(db: Session, e: Experiment, fit_variable: str, model: str,
                       request: Optional[Request] = None) -> dict:
    """Per-experiment parameters used in the comparison (None where not available)."""
    f, err = _fit(db, e.id, fit_variable, model)
    p = f.summary() if f else {}
    df = _readings_df(db, e.id)
    t_od, od = _manual_series(db, e.id, "OD600")
    bd = {}
    if "ph" in channels_of(e) and not df.empty:
        bd = _with_mmol(base_dosing.summarize(df["t_h"].astype(float), df["base_cumulative_ml"].astype(float),
                                              float(od[-1]) if len(od) else None).__dict__, buffer_factor(e))
    cv = _control_values(control.summarize(df, channels_of(e))) if not df.empty else {}
    if "ph" in channels_of(e) and not df.empty:
        bd["t_end_h"] = (_estimated_end(request, df) or {}).get("t_end_h")
    vals = {k: (p.get(k) if src == "fit" else cv.get(k) if src == "control" else bd.get(k)) for k, _, _, src in SESSION_PARAMS}
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


def session_results_data(db: Session, sid: int, step_h: float = 2.0, variable: Optional[str] = None,
                         request: Optional[Request] = None) -> dict:
    sess = _404(db.get(AnalysisSession, sid))
    out = _session_out(sess)
    model, fv = out["model"], sess.fit_variable
    variable = variable or fv
    by_cond: dict[str, list[int]] = {c: [] for c in out["conditions"]}
    for m in sess.members:
        by_cond.setdefault(m.condition, []).append(m.experiment_id)
    exps = {e.id: e for e in db.scalars(select(Experiment).where(Experiment.id.in_(out["members"].keys())))}
    per_exp = {eid: _experiment_values(db, e, fv, model, request) for eid, e in exps.items()}
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
def session_results(sid: int, request: Request, db: Session = Depends(get_db), step_h: float = 2.0,
                    variable: Optional[str] = None):
    if not (0.05 <= step_h <= 24):
        raise HTTPException(422, "Interval out of range")
    res = session_results_data(db, sid, step_h, variable, request)
    # backwards-compatible shape: comparisons keyed by parameter, conditions -> n
    res["comparisons_by_key"] = {c["key"]: c for c in res["comparisons"]}
    return _json_safe(res)


SESSION_COLORS = ["#1d3786", "#c98a00", "#1f7a63", "#7a4fa0", "#b3261e", "#54565a"]
SESSION_MARKERS = ["o", "^", "s", "D", "v", "P"]


@router.get("/analysis-sessions/{sid}/figure.{fmt}")
def session_figure(sid: int, fmt: str, variable: Optional[str] = None, step_h: float = 2.0, width_mm: float = 174,
                   dpi: int = 300, theme: str = "grey", fit: bool = True, mono: bool = False, request: Request = None,
                   db: Session = Depends(get_db)):
    if fmt not in report.FIG_FORMATS:
        raise HTTPException(404, "Format not available")
    if not (40 <= width_mm <= 500) or not (72 <= dpi <= 1200):
        raise HTTPException(422, "Width must be 40-500 mm and resolution 72-1200 dpi")
    res = session_results_data(db, sid, step_h, variable, request)
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
        data = report.render_conditions(series, ylabel, fmt, width_mm, dpi, theme, mono=mono)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return StreamingResponse(io.BytesIO(data), media_type=report.FIG_FORMATS[fmt], headers={
        "Content-Disposition": f'attachment; filename="{res["code"]}-{res["variable"]}{"-bw" if mono else ""}.{fmt}"'})


@router.get("/analysis-sessions/{sid}/boxplot.{fmt}")
def session_boxplot(sid: int, fmt: str, param: str, step_h: float = 2.0, width_mm: float = 85, dpi: int = 300,
                    theme: str = "grey", mono: bool = False, variable: Optional[str] = None,
                    request: Request = None, db: Session = Depends(get_db)):
    """Box-and-whisker of one per-experiment parameter (kinetic or other) by condition, every experiment as a point."""
    if fmt not in report.FIG_FORMATS:
        raise HTTPException(404, "Format not available")
    if not (40 <= width_mm <= 500) or not (72 <= dpi <= 1200):
        raise HTTPException(422, "Width must be 40-500 mm and resolution 72-1200 dpi")
    res = session_results_data(db, sid, step_h, variable, request)
    comp = next((k for k in res["comparisons"] if k["key"] == param), None)
    if comp is None:
        raise HTTPException(422, f"Unknown parameter '{param}'")
    letters = "ABCDEFGHIJ"
    groups = [report.BoxGroup(f"{letters[i]} · {c}", SESSION_COLORS[i % 6], SESSION_MARKERS[i % 6],
                              [x["values"].get(param) for x in res["experiments"] if x["condition"] == c])
              for i, c in enumerate(res["conditions"])]
    ylabel = f"{comp['label']} ({comp['unit']})" if comp["unit"] else comp["label"]
    try:
        data = report.render_box(groups, ylabel, fmt, width_mm, dpi, theme, mono=mono)
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    return StreamingResponse(io.BytesIO(data), media_type=report.FIG_FORMATS[fmt], headers={
        "Content-Disposition": f'attachment; filename="{res["code"]}-box-{param}{"-bw" if mono else ""}.{fmt}"'})


@router.get("/analysis-sessions/{sid}/export.{fmt}")
def session_export(sid: int, fmt: str, request: Request, step_h: float = 2.0, db: Session = Depends(get_db)):
    """Experiments with conditions, per-experiment parameters, comparisons and mean ± SD curves of every variable."""
    if fmt not in ("xlsx", "zip"):
        raise HTTPException(404, "Format not available")
    res = session_results_data(db, sid, step_h, request=request)
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
        r2 = session_results_data(db, sid, step_h, opt["key"], request)
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
                                   for f in res["parallel_run_flags"]) or None,
        "software": f"{citation.TITLE} v{__version__}"}.items()])
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


def _write_export(tables: dict[str, pd.DataFrame], fmt: str, code: str) -> tuple[io.BytesIO, str]:
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
                z.writestr(f"{code}_{name.lower().replace(' ', '_')}.csv", df.to_csv(index=False))
        media = "application/zip"
    buf.seek(0)
    return buf, media


def archive_file_name(e: Experiment, fmt: str, when: Optional[datetime] = None) -> str:
    """'2026-11-10_1432_FM-031_Riboflavin pH 6.0.xlsx': local date and time, code, name (safe for Windows)."""
    when = (when or datetime.now().astimezone()).astimezone()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", e.name).strip(" .-")[:80] or "experiment"
    return f"{when:%Y-%m-%d_%H%M}_{e.code}_{name}.{fmt}"


def save_experiment_copy(db: Session, request: Request, exp_id: int, fmt: Optional[str] = None,
                         blocks=None, resample_min: Optional[float] = None, model: str = "gompertz") -> dict:
    """Write the experiment export into the archive folder (Settings › Data and backup), from the PC itself."""
    e = _404(db.get(Experiment, exp_id))
    cfg = request.app.state.app_settings.get("backup")
    dest = cfg.get("archive_destination", "")
    if not dest:
        raise HTTPException(422, "No archive folder set: Settings › Data and backup (on the lab PC).")
    folder = Path(dest)
    if not folder.is_dir():
        raise HTTPException(502, f"Archive folder not found or not reachable: {dest} (USB stick unplugged?)")
    fmt = fmt or cfg.get("archive_format", "xlsx")
    if fmt not in ("xlsx", "zip"):
        raise HTTPException(422, "Format must be xlsx or zip")
    sel = [b for b in (blocks or EXPORT_BLOCKS) if b in EXPORT_BLOCKS] or list(EXPORT_BLOCKS)
    buf, _ = _write_export(export_tables(db, exp_id, sel, resample_min, model, request), fmt, e.code)
    target = folder / archive_file_name(e, fmt)
    tmp = target.with_name(target.name + ".part")
    try:
        tmp.write_bytes(buf.getvalue())
        os.replace(tmp, target)
    except OSError as ex:
        tmp.unlink(missing_ok=True)
        raise HTTPException(502, f"Could not write to {dest}: {ex}")
    _event(db, e, "archived_copy", f"Copy of {e.code} saved to {target} by {who(request, db)}")
    return {"path": str(target), "bytes": target.stat().st_size}


@router.post("/experiments/{exp_id}/archive-copy")
def archive_copy(exp_id: int, body: S.ArchiveIn, request: Request, db: Session = Depends(get_db)):
    if body.resample_min is not None and not (0.05 <= body.resample_min <= 1440):
        raise HTTPException(422, "Resampling interval out of range")
    return save_experiment_copy(db, request, exp_id, body.format, body.blocks, body.resample_min, body.model)


def export_tables(db: Session, exp_id: int, blocks=EXPORT_BLOCKS, resample_min: Optional[float] = None,
                  model: str = "gompertz", request: Optional[Request] = None) -> dict[str, pd.DataFrame]:
    """One table per export sheet/CSV. Times: hours since start plus absolute UTC timestamps."""
    e = _404(db.get(Experiment, exp_id))
    out: dict[str, pd.DataFrame] = {}
    if "controller" in blocks:
        r = _readings_df(db, exp_id)
        if resample_min and not r.empty:
            r = r.set_index(pd.to_datetime(r["ts"], utc=True)).drop(columns=["ts"]) \
                .apply(pd.to_numeric, errors="coerce").resample(f"{resample_min}min").mean() \
                .dropna(how="all").reset_index()
        f_ = buffer_factor(e)
        if f_ and not r.empty and "base_cumulative_ml" in r:
            r["base_cumulative_mmol"] = pd.to_numeric(r["base_cumulative_ml"], errors="coerce") * f_
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
        rd = _readings_df(db, exp_id)
        if not rd.empty and "ph" in channels_of(e):
            end = _estimated_end(request, rd) or {}
            rows += [{"block": "base dosing", "parameter": "estimated_end_h", "value": end.get("t_end_h")},
                     {"block": "base dosing", "parameter": "estimated_end_rule",
                      "value": f"base rate < {end.get('fraction', 0):.0%} of peak for {end.get('hold_h', 0) * 60:.0f} min"}]
        if not rd.empty:
            ctrl = control.summarize(rd, channels_of(e))
            for lp in ctrl["loops"]:
                rows += [{"block": f"regulation {lp['loop']}", "parameter": k, "value": v}
                         for k, v in lp.items() if k not in ("loop", "label")]
            for name, d in ctrl["doses"].items():
                rows += [{"block": f"dose episodes {name}", "parameter": k, "value": "; ".join(map(str, v)) if isinstance(v, list) else v}
                         for k, v in d.items() if k not in ("counter", "episodes")]
        out["Parameters"] = pd.DataFrame(rows, columns=["block", "parameter", "value"])
    if "metadata" in blocks:
        meta = {"code": e.code, "name": e.name, "strain": e.strain, "reactor": e.reactor_id,
                "profile": e.profile.name if e.profile else (e.plan_snapshot or {}).get("profile"),
                "profile changed after the plan was saved": _exp_out(e).profile_changed or None,
                "status": e.status.value, "channels": ", ".join(channels_of(e)),
                "started_at (UTC)": e.started_at, "ended_at (UTC)": e.ended_at,
                "parallel_run": e.parallel_run.code if e.parallel_run else None,
                "medium": e.medium.name if e.medium else None, "buffer": e.buffer.name if e.buffer else None,
                "buffer concentration (mol/L)": e.buffer_molarity, "buffer equivalents per mole": e.buffer_equivalents if e.buffer_molarity else None,
                "buffer available volume (mL)": e.buffer_volume_ml,
                "working_volume_ml": e.working_volume_ml, "author": e.author, "notes": e.notes}
        rows = [{"field": k, "value": v} for k, v in meta.items()]
        rows += [{"field": f"plan.{k}", "value": v} for k, v in _flatten(e.plan_snapshot)]
        rows.append({"field": "software", "value": f"{citation.TITLE} v{__version__}"})
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
def export_data(exp_id: int, fmt: str, request: Request, db: Session = Depends(get_db), resample_min: Optional[float] = None,
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
    tables = export_tables(db, exp_id, sel, resample_min, model, request)
    buf, media = _write_export(tables, fmt, e.code)
    return StreamingResponse(buf, media_type=media, headers={"Content-Disposition": _attachment(archive_file_name(e, fmt))})


def _attachment(name: str) -> str:
    """Content-Disposition with an ASCII fallback and the UTF-8 name (accents in experiment names)."""
    from urllib.parse import quote
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# --- users (identity for signatures, optional PIN) ---------------------------------------------

def _user_out(u: User, db: Optional[Session] = None) -> dict:
    role = "observer" if u.name == GUEST_NAME else (u.role or "operator")
    out = {"id": u.id, "name": u.name, "has_pin": u.pin_hash is not None, "archived": u.archived,
           "guest": u.name == GUEST_NAME, "role": role}
    if db is not None:
        out["effective_role"] = effective_role(u, db)
    return out


@router.get("/users")
def list_users(db: Session = Depends(get_db), include_archived: bool = False):
    q = select(User).order_by(User.name)
    users = [u for u in db.scalars(q) if include_archived or not u.archived]
    return sorted([_user_out(u, db) for u in users], key=lambda x: (x["guest"], x["name"].lower()))


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
    return {"user": _user_out(u, db) if u else None, "role": effective_role(u, db),
            "admins_exist": admins_exist(db), "client_local": is_local(request)}


@router.post("/users/{uid}/first-admin")
def first_admin(uid: int, request: Request, db: Session = Depends(get_db)):
    """Make the first administrator: only from the lab PC, only while none exists, only for a user with a PIN."""
    if admins_exist(db):
        raise HTTPException(409, "An administrator already exists: ask them to change levels.")
    if not is_local(request):
        raise HTTPException(403, "The first administrator can be set only on the lab PC.")
    u = _404(db.get(User, uid))
    me_ = current_user(request, db)
    if me_ is None or me_.id != u.id:
        raise HTTPException(403, f"Sign in as {u.name} first.")
    if u.name == GUEST_NAME or not u.pin_hash:
        raise HTTPException(422, "Set a PIN first: an administrator needs one.")
    u.role = "admin"
    log.info("first administrator: %s", u.name)
    db.flush()
    return _user_out(u, db)


@router.put("/users/{uid}/role")
def set_role(uid: int, body: S.RoleIn, request: Request, db: Session = Depends(get_db)):
    require_role(request, db, "admin")
    u = _404(db.get(User, uid))
    if body.role not in users_mod.ROLES:
        raise HTTPException(422, "Unknown level.")
    if u.name == GUEST_NAME:
        raise HTTPException(422, "Guest is always an observer.")
    if (u.role == "admin" and body.role != "admin"
            and db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.archived.is_(False))) <= 1):
        raise HTTPException(409, "This is the last administrator: make someone else administrator first.")
    old = u.role or "operator"
    u.role = body.role
    log.info("level of %s: %s -> %s (by %s)", u.name, old, body.role, who(request, db))
    db.add(Event(experiment_id=None, reactor_id="-", kind="user_level", severity="info",
                 message=f"{u.name}: {users_mod.ROLE_LABELS[old]} → {users_mod.ROLE_LABELS[body.role]} (by {who(request, db)})"))
    db.flush()
    return _user_out(u, db)


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
        if body.archived and u.role == "admin" and db.scalar(select(func.count()).select_from(User).where(
                User.role == "admin", User.archived.is_(False))) <= 1:
            raise HTTPException(409, "This is the last administrator and cannot be archived.")
        u.archived = body.archived
    db.flush()
    return {"user": _user_out(u), "token": token}


# --- settings, notifications, backup --------------------------------------------------------

@router.get("/settings")
def get_settings(request: Request):
    return request.app.state.app_settings.public()


@router.put("/settings/{section}")
def put_settings(section: str, body: dict, request: Request):
    if section in LOCAL_ONLY_SECTIONS and not is_local(request):
        raise HTTPException(403, "This section can be changed only on the lab PC.")
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


@router.post("/settings/notifications/telegram-chats")
def find_telegram_chats(body: dict, request: Request):
    """Chats that wrote to the bot: helps to fill in the chat id (the token is the one typed, or the saved one)."""
    from ..notify import telegram_chats
    from ..services.app_settings import normalize_telegram_token, SECRET
    tok = str(body.get("token") or "")
    if not tok or tok == SECRET:
        tok = request.app.state.app_settings.get("notifications")["telegram_token"]
    tok = normalize_telegram_token(tok)
    if not tok:
        raise HTTPException(422, "Enter the bot token first.")
    try:
        chats = telegram_chats(tok)
    except NotifyError as ex:
        raise HTTPException(502, str(ex))
    return {"chats": chats}


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
        "version": __version__, "citation": citation.info(), **build_info(), "client_local": is_local(request), "data_dir": str(st.data_dir), "sampling_interval_s": acq.interval_s,
        "experiments": db.scalar(select(func.count()).select_from(Experiment)),
        "reactors": [{"reactor_id": r.id, "name": r.name, "driver": r.driver, "address": r.address,
                      "online": acq.live[r.id].last_error is None and acq.live[r.id].last_ok is not None,
                      "capabilities": acq.drivers[r.id].capabilities.__dict__}
                     for r in db.scalars(select(Reactor).order_by(Reactor.position, Reactor.id)) if r.id in acq.drivers],
    }


@router.get("/system/status")
def system_status():
    """Server (bioreactor PC) and remote-access state, for the Home page."""
    from ..system_status import remote_access, server_status
    return {"server": {**server_status(), "version": __version__}, "remote": remote_access()}
