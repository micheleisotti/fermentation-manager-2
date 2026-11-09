from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class NamedIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class NamedOut(ORM):
    id: int
    name: str
    description: str
    archived: bool


class VariableIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    unit: str = ""
    category: str


class VariableOut(ORM):
    id: int
    name: str
    unit: str
    category: str


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ph_setpoint: Optional[float] = None
    temperature_setpoint: Optional[float] = None
    stirrer_setpoint: Optional[float] = None
    ph_control_mode: Optional[str] = None
    ph_pid: Optional[dict] = None
    ph_low_limit: Optional[float] = None
    ph_high_limit: Optional[float] = None
    temperature_low_limit: Optional[float] = None
    temperature_high_limit: Optional[float] = None
    notes: str = ""
    author: str = ""


class ProfileOut(ORM):
    id: int
    name: str
    ph_setpoint: Optional[float]
    temperature_setpoint: Optional[float]
    stirrer_setpoint: Optional[float]
    ph_control_mode: Optional[str]
    ph_pid: Optional[dict]
    ph_low_limit: Optional[float] = None
    ph_high_limit: Optional[float] = None
    temperature_low_limit: Optional[float] = None
    temperature_high_limit: Optional[float] = None
    notes: str


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    reactor_id: str
    planned_date: Optional[str] = None
    strain: str = ""
    medium_id: Optional[int] = None
    buffer_id: Optional[int] = None
    working_volume_ml: Optional[float] = None
    profile_id: Optional[int] = None
    channels: Optional[list[str]] = None
    notes: str = ""
    author: str = ""


class ExperimentPatch(BaseModel):
    name: Optional[str] = None
    planned_date: Optional[str] = None
    strain: Optional[str] = None
    medium_id: Optional[int] = None
    buffer_id: Optional[int] = None
    working_volume_ml: Optional[float] = None
    profile_id: Optional[int] = None
    reactor_id: Optional[str] = None
    channels: Optional[list[str]] = None
    notes: Optional[str] = None


class ExperimentOut(ORM):
    id: int
    code: str
    name: str
    planned_date: Optional[str]
    strain: str
    medium_id: Optional[int]
    buffer_id: Optional[int]
    working_volume_ml: Optional[float]
    reactor_id: str
    status: str
    profile_id: Optional[int]
    parallel_run_code: Optional[str] = None
    parallel_partner_codes: list[str] = []
    channels: Optional[list[str]] = None      # filled by _exp_out (default set when None)
    notes: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class ParallelIn(BaseModel):
    partner_id: int


class MeasurementIn(BaseModel):
    variable_id: int
    value: float
    sampled_at: Optional[datetime] = None   # default: now
    unit: str = ""
    note: str = ""
    author: str = ""


class MeasurementPatch(BaseModel):
    value: Optional[float] = None
    sampled_at: Optional[datetime] = None
    unit: Optional[str] = None
    note: Optional[str] = None
    author: str = ""


class MeasurementOut(ORM):
    id: int
    experiment_id: int
    variable_id: int
    variable_name: str
    sampled_at: datetime
    t_h: float
    value: float
    unit: str
    note: str
    author: str


class LimitsIn(BaseModel):
    parameter: str                      # "ph" | "temperature"
    low: Optional[float] = None
    high: Optional[float] = None
    origin: str = "local"
    author: str = ""


class SetpointIn(BaseModel):
    parameter: str
    value: float
    origin: str = "local"
    author: str = ""


class EventOut(ORM):
    id: int
    experiment_id: Optional[int]
    reactor_id: str
    kind: str
    severity: str
    message: str
    t_h: Optional[float]
    at: datetime


class SessionIn(BaseModel):
    name: str
    members: dict[int, str]   # experiment_id -> condition label
    conditions: Optional[list[str]] = None   # ordered labels; default: order of first appearance in members
    fit_variable: str = "OD600"
    model: str = "gompertz"
    author: str = ""


class UserIn(BaseModel):
    name: str
    pin: Optional[str] = None


class UserPatch(BaseModel):
    name: Optional[str] = None
    pin: Optional[str] = None        # "" removes the PIN
    archived: Optional[bool] = None


class LoginIn(BaseModel):
    user_id: int
    pin: Optional[str] = None
