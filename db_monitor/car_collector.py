from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from db_monitor.config import CarRoute, Settings
from db_monitor.models import CarObservation


def _fetch_route_duration(settings: Settings, route: CarRoute) -> tuple[int, float]:
    if not settings.ors_api_key:
        raise ValueError("Set ORS_API_KEY to collect car travel times.")

    headers = {
        "Authorization": settings.ors_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "coordinates": [
            [route.from_lon, route.from_lat],
            [route.to_lon, route.to_lat],
        ]
    }

    response = requests.post(
        settings.ors_directions_endpoint,
        json=payload,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    summary = data["routes"][0]["summary"]
    duration_minutes = int(round(float(summary["duration"]) / 60.0))
    distance_km = round(float(summary["distance"]) / 1000.0, 1)
    return duration_minutes, distance_km


def collect_car_observations(settings: Settings, routes: list[CarRoute]) -> list[CarObservation]:
    if not settings.ors_api_key:
        return []

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    service_date = now.date().isoformat()

    rows: list[CarObservation] = []
    for route in routes:
        # Only collect a route once its relevant departure time has started.
        # Example: Offenburg->Freiburg should not be collected before 16:30.
        if now.time() < route.target_departure:
            continue

        duration, distance_km = _fetch_route_duration(settings, route)
        rows.append(
            CarObservation(
                observation_ts=now,
                service_date=service_date,
                route_label=route.label,
                from_name=route.from_name,
                to_name=route.to_name,
                target_departure_time=route.target_departure.strftime("%H:%M"),
                duration_minutes=duration,
                distance_km=distance_km,
            )
        )

    return rows
