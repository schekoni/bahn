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
    ors_api_key: str
    ors_directions_endpoint: str


@dataclass(frozen=True)
class CarRoute:
    label: str
    from_name: str
    to_name: str
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    target_departure: time


def _parse_time(raw: str) -> time:
    hh, mm = raw.split(":", maxsplit=1)
    return time(hour=int(hh), minute=int(mm))


def _parse_float(raw: str) -> float:
    return float(raw.strip())


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
        ors_api_key=os.getenv("ORS_API_KEY", "").strip(),
        ors_directions_endpoint=os.getenv(
            "ORS_DIRECTIONS_ENDPOINT", "https://api.openrouteservice.org/v2/directions/driving-car"
        ),
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


def load_car_routes() -> list[CarRoute]:
    return [
        CarRoute(
            label="Car Morning Freiburg->Offenburg",
            from_name=os.getenv("CAR_MORNING_FROM_NAME", "Freiburg Egonstrasse"),
            to_name=os.getenv("CAR_MORNING_TO_NAME", "Offenburg Ebertplatz"),
            from_lat=_parse_float(os.getenv("CAR_MORNING_FROM_LAT", "47.9996")),
            from_lon=_parse_float(os.getenv("CAR_MORNING_FROM_LON", "7.8419")),
            to_lat=_parse_float(os.getenv("CAR_MORNING_TO_LAT", "48.4730")),
            to_lon=_parse_float(os.getenv("CAR_MORNING_TO_LON", "7.9468")),
            target_departure=_parse_time(os.getenv("CAR_MORNING_TIME", "06:45")),
        ),
        CarRoute(
            label="Car Afternoon Offenburg->Freiburg",
            from_name=os.getenv("CAR_AFTERNOON_FROM_NAME", "Offenburg Ebertplatz"),
            to_name=os.getenv("CAR_AFTERNOON_TO_NAME", "Freiburg Egonstrasse"),
            from_lat=_parse_float(os.getenv("CAR_AFTERNOON_FROM_LAT", "48.4730")),
            from_lon=_parse_float(os.getenv("CAR_AFTERNOON_FROM_LON", "7.9468")),
            to_lat=_parse_float(os.getenv("CAR_AFTERNOON_TO_LAT", "47.9996")),
            to_lon=_parse_float(os.getenv("CAR_AFTERNOON_TO_LON", "7.8419")),
            target_departure=_parse_time(os.getenv("CAR_AFTERNOON_TIME", "16:30")),
        ),
    ]
