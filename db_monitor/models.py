from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PlannedStop:
    train_id: str
    train_name: str
    line: str
    source_station: str
    target_station: str
    planned_departure: datetime | None
    planned_arrival: datetime | None
    route_label: str


@dataclass(frozen=True)
class ChangeInfo:
    train_id: str
    changed_departure: datetime | None
    changed_arrival: datetime | None
    departure_reason: str
    arrival_reason: str
    canceled: bool


@dataclass(frozen=True)
class Observation:
    observation_ts: datetime
    service_date: str
    train_id: str
    train_name: str
    line: str
    route_label: str
    source_station: str
    target_station: str
    planned_departure: datetime
    actual_departure: datetime | None
    planned_arrival: datetime | None
    actual_arrival: datetime | None
    delay_minutes: int
    schedule_deviation_minutes: int
    arrival_delay_minutes: int
    arrival_schedule_deviation_minutes: int
    arrival_observed: bool
    arrival_info_missing: bool
    departure_reason: str
    arrival_reason: str
    canceled_departure: bool
    canceled_arrival: bool
    canceled: bool
