"""Pydantic request/response schemas for the GOpti API."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, validator


class StartPoint(BaseModel):
    lat: float
    lng: float
    time: datetime


class SolveEvent(BaseModel):
    id: str
    dwell_min: Optional[int] = Field(default=None, ge=1)


class SolveRequest(BaseModel):
    start: StartPoint
    endTime: datetime
    events: List[SolveEvent] = Field(default_factory=list, max_items=24)
    walkingSpeed: float = Field(default=1.35, gt=0.05, le=3.0)
    compressDwellToMin: bool = False

    @validator("endTime")
    def validate_end_after_start(cls, v: datetime, values: Dict[str, object]):
        start: StartPoint = values.get("start")  # type: ignore[assignment]
        if start and v <= start.time:
            raise ValueError("endTime must be after start.time")
        return v

    @validator("events")
    def validate_events(cls, v: List[SolveEvent]):
        if not v:
            raise ValueError("events list must not be empty")
        event_ids = {e.id for e in v}
        if len(event_ids) != len(v):
            raise ValueError("duplicate event ids in request")
        return v


class StopOut(BaseModel):
    eventId: str
    sessionStart: datetime
    sessionEnd: datetime
    arrive: datetime
    depart: datetime
    dwellSec: int
    travelSecFromPrev: int
    venue: Dict[str, object]
    polyline: Optional[str] = None
    source: Dict[str, object] = Field(default_factory=dict)


class DroppedReason(BaseModel):
    eventId: str
    reason: str
    sessionsConsidered: Optional[int] = None
    detail: Dict[str, object] = Field(default_factory=dict)


class SolveMetrics(BaseModel):
    visited: int
    dropped: int
    totalWalkSec: int
    solver: str
    solveMs: Optional[int] = None


class SolveResponse(BaseModel):
    route: List[StopOut]
    dropped: List[DroppedReason]
    metrics: SolveMetrics


class DebugSolveResponse(SolveResponse):
    nodes: List[Dict[str, object]] = Field(default_factory=list)
    matrixMeta: Dict[str, object] = Field(default_factory=dict)
