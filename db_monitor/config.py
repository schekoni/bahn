from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time

from dotenv import load_dotenv


@dataclass(frozen=True)
class RouteWindow:
    label: str
    source_station: str
    target_station: str
    source_eva: str
    target_eva: str
    start_time: time
    end_time: time


@dataclass(frozen=True)
class Settings:
    client_id: str
    api_key: str
    timezone: str
    station_endpoint: str
    timetables_endpoint: str
    database_path: str


def _parse_time(raw: str) -> time:
    hh, mm = raw.split(":", maxsplit=1)
    return time(hour=int(hh), minute=int(mm))


def load_settings() -> Settings:
    load_dotenv()

    client_id = os.getenv("DB_CLIENT_ID", "").strip()
    api_key = os.getenv("DB_API_KEY", "").strip()
    # Backward compatibility with earlier variable naming in this project.
    if not api_key:
        api_key = os.getenv("DB_CLIENT_SECRET", "").strip()
    if not client_id or not api_key:
        raise ValueError("Set DB_CLIENT_ID and DB_API_KEY in your environment.")

    return Settings(
        client_id=client_id,
        api_key=api_key,
        timezone=os.getenv("TIMEZONE", "Europe/Berlin"),
        station_endpoint=os.getenv(
            "DB_STATION_ENDPOINT", "https://apis.deutschebahn.com/db-api-marketplace/apis/station-data/v2"
        ),
        timetables_endpoint=os.getenv(
            "DB_TIMETABLES_ENDPOINT", "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"
        ),
        database_path=os.getenv("DATABASE_PATH", "data/train_punctuality.db"),
    )


def load_route_windows() -> list[RouteWindow]:
    return [
        RouteWindow(
            label="Morning Freiburg->Offenburg",
            source_station=os.getenv("MORNING_SOURCE", "Freiburg(Breisgau) Hbf"),
            target_station=os.getenv("MORNING_TARGET", "Offenburg"),
            source_eva=os.getenv("MORNING_SOURCE_EVA", "8000107"),
            target_eva=os.getenv("MORNING_TARGET_EVA", "8000290"),
            start_time=_parse_time(os.getenv("MORNING_START", "06:00")),
            end_time=_parse_time(os.getenv("MORNING_END", "08:00")),
        ),
        RouteWindow(
            label="Afternoon Offenburg->Freiburg",
            source_station=os.getenv("AFTERNOON_SOURCE", "Offenburg"),
            target_station=os.getenv("AFTERNOON_TARGET", "Freiburg(Breisgau) Hbf"),
            source_eva=os.getenv("AFTERNOON_SOURCE_EVA", "8000290"),
            target_eva=os.getenv("AFTERNOON_TARGET_EVA", "8000107"),
            start_time=_parse_time(os.getenv("AFTERNOON_START", "15:30")),
            end_time=_parse_time(os.getenv("AFTERNOON_END", "17:30")),
        ),
    ]
