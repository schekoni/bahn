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
    openboards_endpoint: str
    db_api_provider: str
    database_path: str
    car_provider: str
    ors_api_key: str
    ors_directions_endpoint: str
    tomtom_api_key: str
    tomtom_routing_endpoint: str


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


def _env(name: str, default: str) -> str:
    # GitHub passes an unset secret as an empty string; treat it as missing.
    return (os.getenv(name) or "").strip() or default


# The home address is private: set CAR_HOME_* in .env / GitHub secrets. The
# repository only carries a neutral fallback (Freiburg Hbf).
HOME_FALLBACK = ("Freiburg Hbf", "47.9977", "7.8414")
WORK = ("Offenburg Ebertplatz", "48.4730", "7.9468")


def load_settings() -> Settings:
    load_dotenv()

    client_id = os.getenv("DB_CLIENT_ID", "").strip()
    api_key = os.getenv("DB_API_KEY", "").strip()
    # Backward compatibility with earlier variable naming in this project.
    if not api_key:
        api_key = os.getenv("DB_CLIENT_SECRET", "").strip()

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
        openboards_endpoint=os.getenv(
            "DB_OPENBOARDS_ENDPOINT", "https://apis.deutschebahn.com/db-api-marketplace/apis/open-boards/v1"
        ),
        db_api_provider=os.getenv("DB_API_PROVIDER", "openboards").strip().lower(),
        database_path=os.getenv("DATABASE_PATH", "data/train_punctuality.db"),
        car_provider=os.getenv("CAR_PROVIDER", "ors").strip().lower(),
        ors_api_key=os.getenv("ORS_API_KEY", "").strip(),
        ors_directions_endpoint=os.getenv(
            "ORS_DIRECTIONS_ENDPOINT", "https://api.openrouteservice.org/v2/directions/driving-car"
        ),
        tomtom_api_key=os.getenv("TOMTOM_API_KEY", "").strip(),
        tomtom_routing_endpoint=os.getenv(
            "TOMTOM_ROUTING_ENDPOINT", "https://api.tomtom.com/routing/1/calculateRoute"
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
    home_name = _env("CAR_HOME_NAME", "Zuhause" if os.getenv("CAR_HOME_LAT") else HOME_FALLBACK[0])
    home_lat = _env("CAR_HOME_LAT", HOME_FALLBACK[1])
    home_lon = _env("CAR_HOME_LON", HOME_FALLBACK[2])
    return [
        CarRoute(
            label="Car Morning Freiburg->Offenburg",
            from_name=_env("CAR_MORNING_FROM_NAME", home_name),
            to_name=_env("CAR_MORNING_TO_NAME", WORK[0]),
            from_lat=_parse_float(_env("CAR_MORNING_FROM_LAT", home_lat)),
            from_lon=_parse_float(_env("CAR_MORNING_FROM_LON", home_lon)),
            to_lat=_parse_float(_env("CAR_MORNING_TO_LAT", WORK[1])),
            to_lon=_parse_float(_env("CAR_MORNING_TO_LON", WORK[2])),
            target_departure=_parse_time(_env("CAR_MORNING_TIME", "06:30")),
        ),
        CarRoute(
            label="Car Afternoon Offenburg->Freiburg",
            from_name=_env("CAR_AFTERNOON_FROM_NAME", WORK[0]),
            to_name=_env("CAR_AFTERNOON_TO_NAME", home_name),
            from_lat=_parse_float(_env("CAR_AFTERNOON_FROM_LAT", WORK[1])),
            from_lon=_parse_float(_env("CAR_AFTERNOON_FROM_LON", WORK[2])),
            to_lat=_parse_float(_env("CAR_AFTERNOON_TO_LAT", home_lat)),
            to_lon=_parse_float(_env("CAR_AFTERNOON_TO_LON", home_lon)),
            target_departure=_parse_time(_env("CAR_AFTERNOON_TIME", "16:30")),
        ),
    ]
