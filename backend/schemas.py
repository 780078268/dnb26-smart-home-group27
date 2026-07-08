from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Role = Literal["student", "teacher", "guest"]
CommandType = Literal["SET_LIGHT", "SET_FAN", "OPEN_DOOR", "CLOSE_DOOR", "REQUEST_PHOTO"]
CommandStatus = Literal["pending", "sent", "done", "failed"]
AckStatus = Literal["done", "failed"]
AccessDecision = Literal["allow", "deny", "unknown"]


class TelemetryInput(BaseModel):
    device_id: str
    captured_at: str | None = None
    temperature_c: float
    door_open: bool | None = None
    window_open: bool | None = None
    light_level: int | None = Field(default=None, ge=0, le=100)
    fan_on: bool | None = None


class PersonCreate(BaseModel):
    name: str
    role: Role = "student"
    authorized: bool = True


class PersonPatch(BaseModel):
    name: str | None = None
    role: Role | None = None
    authorized: bool | None = None


class CommandCreate(BaseModel):
    device_id: str
    type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAck(BaseModel):
    device_id: str
    status: AckStatus
    message: str | None = None

