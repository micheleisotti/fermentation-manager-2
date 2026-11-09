"""
Database schema.

Design rules agreed during the mockup phase:
- An Experiment is always a standalone unit. Time is always "hours since Start".
- A ParallelRun is only a logistic link (live monitoring, R1|R2 switching); it never
  defines conditions or replicates.
- Conditions and replicates live in AnalysisSession / SessionMember, so the same
  experiment can be grouped differently in different sessions.
- Controller readings are immutable. Manual measurements are editable, and every
  edit is logged (author + timestamp).
- Every user-originated row stores its author, so multi-user roles can be added later.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ExperimentStatus(str, enum.Enum):
    planned = "planned"
    fermenting = "fermenting"
    completed = "completed"
    aborted = "aborted"


class VariableCategory(str, enum.Enum):
    """Same semantics as v1: the category decides how a variable enters calculations."""
    biomass = "biomass"
    product = "product"
    substrate = "substrate"
    ph = "ph"
    buffer = "buffer"
    other = "other"


class PhControlMode(str, enum.Enum):
    adaptive = "adaptive"
    pid = "pid"


# --- catalogues ---------------------------------------------------------------

class Medium(Base):
    __tablename__ = "media"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Buffer(Base):
    __tablename__ = "buffers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Variable(Base):
    """Catalogue of manually measured variables (OD600, AFU, riboflavin, ...)."""
    __tablename__ = "variables"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    unit: Mapped[str] = mapped_column(String(40), default="")
    category: Mapped[VariableCategory] = mapped_column(Enum(VariableCategory))


class ControllerProfile(Base):
    """Named set of setpoints + pH control settings, loadable onto a controller at planning."""
    __tablename__ = "controller_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    ph_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    temperature_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    stirrer_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    ph_control_mode: Mapped[Optional[PhControlMode]] = mapped_column(Enum(PhControlMode))
    # Parameter names depend on what ez2-Control exposes: kept as a free dict until verified.
    ph_pid: Mapped[Optional[dict]] = mapped_column(JSON)
    # Process (alarm) limits loaded with the profile (None = leave the controller's value)
    ph_low_limit: Mapped[Optional[float]] = mapped_column(Float)
    ph_high_limit: Mapped[Optional[float]] = mapped_column(Float)
    temperature_low_limit: Mapped[Optional[float]] = mapped_column(Float)
    temperature_high_limit: Mapped[Optional[float]] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- experiments --------------------------------------------------------------

class ParallelRun(Base):
    __tablename__ = "parallel_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "PR-012"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="parallel_run")


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "FM-031"
    name: Mapped[str] = mapped_column(String(300))
    planned_date: Mapped[Optional[str]] = mapped_column(String(10))  # ISO date, editable
    strain: Mapped[str] = mapped_column(String(100), default="")
    medium_id: Mapped[Optional[int]] = mapped_column(ForeignKey("media.id"))
    buffer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("buffers.id"))
    working_volume_ml: Mapped[Optional[float]] = mapped_column(Float)
    reactor_id: Mapped[str] = mapped_column(String(10))  # "R1" | "R2"
    status: Mapped[ExperimentStatus] = mapped_column(Enum(ExperimentStatus), default=ExperimentStatus.planned)
    profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("controller_profiles.id"))
    # Plan as loaded at Start (frozen copy, so later profile edits don't rewrite history).
    plan_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    parallel_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("parallel_runs.id"))
    # Channels in use (drivers.base.CHANNELS); None = the default set (T, pH, stirrer)
    channels: Mapped[Optional[list]] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # t = 0
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    author: Mapped[str] = mapped_column(String(100), default="")

    medium: Mapped[Optional[Medium]] = relationship()
    buffer: Mapped[Optional[Buffer]] = relationship()
    profile: Mapped[Optional[ControllerProfile]] = relationship()
    parallel_run: Mapped[Optional[ParallelRun]] = relationship(back_populates="experiments")


class ControllerReading(Base):
    """One poll of one controller during an experiment. Immutable."""
    __tablename__ = "controller_readings"
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    t_h: Mapped[float] = mapped_column(Float)  # hours since Start
    ph: Mapped[Optional[float]] = mapped_column(Float)
    ph_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    temperature: Mapped[Optional[float]] = mapped_column(Float)
    temperature_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    stirrer: Mapped[Optional[float]] = mapped_column(Float)
    stirrer_setpoint: Mapped[Optional[float]] = mapped_column(Float)
    base_cumulative_ml: Mapped[Optional[float]] = mapped_column(Float)  # controller dose monitoring
    base_pump_pct: Mapped[Optional[float]] = mapped_column(Float)
    ph_low_limit: Mapped[Optional[float]] = mapped_column(Float)
    ph_high_limit: Mapped[Optional[float]] = mapped_column(Float)
    temperature_low_limit: Mapped[Optional[float]] = mapped_column(Float)
    temperature_high_limit: Mapped[Optional[float]] = mapped_column(Float)
    # Optional channels (drivers.base.EXTRA_FIELDS): dO2, heating/cooling, stirrer output, gases
    extra: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (Index("ix_readings_exp_t", "experiment_id", "t_h"),)


class ManualMeasurement(Base):
    __tablename__ = "manual_measurements"
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    variable_id: Mapped[int] = mapped_column(ForeignKey("variables.id"))
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    t_h: Mapped[float] = mapped_column(Float)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    variable: Mapped[Variable] = relationship()


class ManualMeasurementEdit(Base):
    """Audit trail for edits to manual measurements."""
    __tablename__ = "manual_measurement_edits"
    id: Mapped[int] = mapped_column(primary_key=True)
    measurement_id: Mapped[int] = mapped_column(ForeignKey("manual_measurements.id", ondelete="CASCADE"))
    old: Mapped[dict] = mapped_column(JSON)
    new: Mapped[dict] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(100), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SetpointCommand(Base):
    """Change log of setpoints written from the software (local or remote)."""
    __tablename__ = "setpoint_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("experiments.id"))
    reactor_id: Mapped[str] = mapped_column(String(10))
    parameter: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[Optional[float]] = mapped_column(Float)
    requested_value: Mapped[float] = mapped_column(Float)
    readback_value: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))  # "confirmed" | "mismatch" | "rejected" | "error"
    message: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(20), default="local")  # "local" | "remote" | "profile"
    author: Mapped[str] = mapped_column(String(100), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """Notified events (alerts and informational), shown in 'Recent events'."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    reactor_id: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # "alert" | "info" | "good"
    message: Mapped[str] = mapped_column(Text)
    t_h: Mapped[Optional[float]] = mapped_column(Float)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


# --- analysis -----------------------------------------------------------------

class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # "AS-007"
    name: Mapped[str] = mapped_column(String(300))
    fit_variable: Mapped[str] = mapped_column(String(100), default="OD600")
    model: Mapped[str] = mapped_column(String(40), default="gompertz")
    # Ordered condition labels (order = A, B, C ... in the UI); may include conditions with no experiment yet
    conditions: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    author: Mapped[str] = mapped_column(String(100), default="")
    members: Mapped[list["SessionMember"]] = relationship(cascade="all, delete-orphan")


class SessionMember(Base):
    __tablename__ = "session_members"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("analysis_sessions.id", ondelete="CASCADE"))
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"))
    condition: Mapped[str] = mapped_column(String(100))  # label, e.g. "A · Lp187_B2"
    __table_args__ = (UniqueConstraint("session_id", "experiment_id"),)


DEFAULT_VARIABLES = [
    ("OD600", "AU", VariableCategory.biomass),
    ("AFU", "AFU", VariableCategory.product),
    ("Riboflavin", "mg/L", VariableCategory.product),
    ("pH (manual)", "", VariableCategory.ph),
]


class AppSetting(Base):
    """Settings edited from the Settings page (one JSON value per section)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base):
    """
    People who use the app. Identity only (no permission levels): the chosen user signs
    experiments, measurements and commands. An optional PIN stops others from signing
    as that user; "Guest" never has a PIN.
    """
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    pin_hash: Mapped[Optional[str]] = mapped_column(String(200))   # "salt$hash" (PBKDF2-SHA256), None = no PIN
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bumped on PIN change: old sign-ins expire
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


GUEST_NAME = "Guest"
