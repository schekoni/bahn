from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from db_monitor.config import CarRoute, Settings
from db_monitor.models import CarObservation


def _fetch_route_duration_ors(settings: Settings, route: CarRoute) -> tuple[int, float]:
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


def _fetch_route_duration_tomtom(
    settings: Settings, route: CarRoute, depart_at: datetime | None = None
) -> tuple[int, float]:
    if not settings.tomtom_api_key:
        raise ValueError("Set TOMTOM_API_KEY to collect car travel times with TomTom.")

    coordinates = f"{route.from_lat},{route.from_lon}:{route.to_lat},{route.to_lon}"
    response = requests.get(
        f"{settings.tomtom_routing_endpoint}/{coordinates}/json",
        params={
            "key": settings.tomtom_api_key,
            "traffic": "true",
            # A future departure makes TomTom use historic traffic speed profiles.
            "departAt": depart_at.isoformat() if depart_at else "now",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    summary = data["routes"][0]["summary"]
    duration_minutes = int(round(float(summary["travelTimeInSeconds"]) / 60.0))
    distance_km = round(float(summary["lengthInMeters"]) / 1000.0, 1)
    return duration_minutes, distance_km


def _fetch_route_duration(
    settings: Settings, route: CarRoute, depart_at: datetime | None = None
) -> tuple[int, float]:
    provider = settings.car_provider
    if provider == "tomtom":
        return _fetch_route_duration_tomtom(settings, route, depart_at)
    if provider in {"ors", "openrouteservice"}:
        if depart_at is not None:
            raise ValueError("OpenRouteService has no traffic forecast for future departures.")
        return _fetch_route_duration_ors(settings, route)
    raise ValueError(f"Unsupported CAR_PROVIDER '{provider}'. Use 'tomtom' or 'ors'.")


# A live sample only reflects the commute if taken close to the departure time;
# a sample from 90 minutes later would replace a better forecast for 16:30.
LIVE_WINDOW = timedelta(minutes=30)


def collect_car_observations(
    settings: Settings,
    routes: list[CarRoute],
    existing_targets: set[tuple[str, str]] | None = None,
    now: datetime | None = None,
) -> list[CarObservation]:
    """Measure live around the commute time, otherwise store a forecast.

    GitHub's scheduler starts runs at unpredictable times, so a live sample
    inside the 30-minute window is luck. Outside it, TomTom's prediction for the
    next upcoming departure keeps a value for every day; a later live sample for
    the same day replaces it (see ObservationStore.upsert_car_many).

    existing_targets: (route_label, HH:MM) pairs that already have a *live*
    measurement today.
    """
    if settings.car_provider == "tomtom" and not settings.tomtom_api_key:
        return []
    if settings.car_provider in {"ors", "openrouteservice"} and not settings.ors_api_key:
        return []

    tz = ZoneInfo(settings.timezone)
    now = now or datetime.now(tz)
    existing = existing_targets or set()
    can_forecast = settings.car_provider == "tomtom"
    rows: list[CarObservation] = []
    for route in routes:
        target_hhmm = route.target_departure.strftime("%H:%M")
        target_today = datetime.combine(now.date(), route.target_departure, tzinfo=tz)

        if target_today <= now <= target_today + LIVE_WINDOW:
            if (route.label, target_hhmm) in existing:
                continue
            depart_at, source = None, "live"
            service_date = now.date()
        elif not can_forecast:
            continue
        elif now < target_today:
            depart_at, source = target_today, "forecast"
            service_date = now.date()
        else:
            depart_at, source = target_today + timedelta(days=1), "forecast"
            service_date = depart_at.date()

        if service_date.weekday() >= 5:
            # Commute comparison only: weekend traffic says nothing about it.
            continue

        duration, distance_km = _fetch_route_duration(settings, route, depart_at)
        rows.append(
            CarObservation(
                observation_ts=now,
                service_date=service_date.isoformat(),
                route_label=route.label,
                from_name=route.from_name,
                to_name=route.to_name,
                target_departure_time=target_hhmm,
                duration_minutes=duration,
                distance_km=distance_km,
                source=source,
            )
        )
    return rows
