from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from db_monitor.client import DBApiClient
from db_monitor.config import RouteWindow, Settings
from db_monitor.models import Observation, PlannedStop
from db_monitor.parser import parse_arrivals_plan, parse_changes, parse_departures_plan


def _hour_range(window: RouteWindow) -> list[int]:
    return list(range(window.start_time.hour, window.end_time.hour + 1))


def _arrival_hour_range(window: RouteWindow, lookahead_hours: int = 3) -> list[int]:
    start = window.start_time.hour
    end = min(23, window.end_time.hour + lookahead_hours)
    return list(range(start, end + 1))


def _minutes_delta(actual: datetime | None, planned: datetime | None) -> int:
    if actual is None or planned is None:
        return 0
    return int((actual - planned).total_seconds() / 60)


def _match_arrival_for_departure(
    departure: PlannedStop,
    arrivals: list[PlannedStop],
    used_ids: set[str],
) -> PlannedStop | None:
    if departure.train_id:
        for candidate in arrivals:
            if candidate.train_id == departure.train_id and candidate.train_id not in used_ids:
                used_ids.add(candidate.train_id)
                return candidate

    same_name = [x for x in arrivals if x.train_name == departure.train_name and x.train_id not in used_ids]
    if not same_name:
        return None

    same_name.sort(key=lambda x: x.planned_arrival or datetime.max)
    if departure.planned_departure is None:
        chosen = same_name[0]
        used_ids.add(chosen.train_id)
        return chosen

    # Prefer arrival closest after departure; fallback to nearest by absolute distance.
    preferred: PlannedStop | None = None
    preferred_minutes = 10**9
    fallback = same_name[0]
    fallback_abs = 10**9

    for candidate in same_name:
        if candidate.planned_arrival is None:
            continue
        diff = int((candidate.planned_arrival - departure.planned_departure).total_seconds() / 60)
        abs_diff = abs(diff)
        if abs_diff < fallback_abs:
            fallback = candidate
            fallback_abs = abs_diff
        if 0 <= diff <= 300 and diff < preferred_minutes:
            preferred = candidate
            preferred_minutes = diff

    chosen = preferred or fallback
    used_ids.add(chosen.train_id)
    return chosen


def collect_observations(settings: Settings, windows: list[RouteWindow]) -> list[Observation]:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    now_local_naive = now.replace(tzinfo=None)
    service_date = now.date()

    client = DBApiClient(settings)
    observations: list[Observation] = []

    for window in windows:
        source_eva = window.source_eva or client.get_station_eva(window.source_station)
        target_eva = window.target_eva or client.get_station_eva(window.target_station)

        source_changes = parse_changes(client.get_changes(source_eva))
        target_changes = parse_changes(client.get_changes(target_eva))

        departures: list[PlannedStop] = []
        for hour in _hour_range(window):
            departures.extend(
                parse_departures_plan(
                    xml_payload=client.get_plan(source_eva, service_date, hour),
                    source_station=window.source_station,
                    target_station=window.target_station,
                    route_label=window.label,
                    window_start=window.start_time,
                    window_end=window.end_time,
                )
            )

        arrival_start = window.start_time
        arrival_end = (datetime.combine(service_date, window.end_time) + timedelta(hours=3)).time()

        arrivals: list[PlannedStop] = []
        for hour in _arrival_hour_range(window):
            arrivals.extend(
                parse_arrivals_plan(
                    xml_payload=client.get_plan(target_eva, service_date, hour),
                    source_station=window.source_station,
                    target_station=window.target_station,
                    route_label=window.label,
                    window_start=arrival_start,
                    window_end=arrival_end,
                )
            )

        departures.sort(key=lambda x: x.planned_departure or datetime.max)
        arrivals.sort(key=lambda x: x.planned_arrival or datetime.max)
        used_arrival_ids: set[str] = set()

        for dep in departures:
            dep_change = source_changes.get(dep.train_id)
            matched_arr = _match_arrival_for_departure(dep, arrivals, used_arrival_ids)
            arr_change = target_changes.get(matched_arr.train_id) if matched_arr else None

            actual_departure = dep_change.changed_departure if dep_change else None

            dep_deviation = _minutes_delta(actual_departure, dep.planned_departure)

            canceled_departure = dep_change.canceled if dep_change else False
            departure_reason = (dep_change.departure_reason if dep_change else "").strip()

            if canceled_departure and not departure_reason:
                departure_reason = "Ausfall"

            if dep.planned_departure is None:
                continue

            planned_arrival = matched_arr.planned_arrival if matched_arr else None
            arrival_deadline = planned_arrival + timedelta(hours=1) if planned_arrival else None
            within_capture_window = bool(arrival_deadline and now_local_naive <= arrival_deadline)
            arrival_event_available = bool(
                arr_change
                and (
                    arr_change.changed_arrival is not None
                    or arr_change.canceled
                )
            )

            # Treat explicit arrival events as observed immediately.
            # Otherwise, only infer an observed on-time arrival once planned arrival
            # has passed and we are still inside the 1h capture window.
            arrival_observed = bool(
                arrival_event_available
                or (
                    within_capture_window
                    and planned_arrival is not None
                    and matched_arr is not None
                    and now_local_naive >= planned_arrival
                )
            )

            if arrival_observed:
                actual_arrival = arr_change.changed_arrival if (arr_change and arr_change.changed_arrival) else planned_arrival
                arr_deviation = _minutes_delta(actual_arrival, planned_arrival)
                canceled_arrival = arr_change.canceled if arr_change else False
                arrival_reason = (arr_change.arrival_reason if arr_change else "").strip()
                if canceled_arrival and not arrival_reason:
                    arrival_reason = "Ausfall"
            else:
                actual_arrival = None
                arr_deviation = 0
                canceled_arrival = False
                arrival_reason = ""

            arrival_info_missing = bool(planned_arrival and not arrival_observed and now_local_naive > arrival_deadline)
            canceled_any = canceled_departure or canceled_arrival

            observations.append(
                Observation(
                    observation_ts=now,
                    service_date=service_date.isoformat(),
                    train_id=dep.train_id,
                    train_name=dep.train_name,
                    line=dep.line,
                    route_label=dep.route_label,
                    source_station=dep.source_station,
                    target_station=dep.target_station,
                    planned_departure=dep.planned_departure,
                    actual_departure=actual_departure,
                    planned_arrival=planned_arrival,
                    actual_arrival=actual_arrival,
                    delay_minutes=max(0, dep_deviation),
                    schedule_deviation_minutes=dep_deviation,
                    arrival_delay_minutes=max(0, arr_deviation),
                    arrival_schedule_deviation_minutes=arr_deviation,
                    arrival_observed=arrival_observed,
                    arrival_info_missing=arrival_info_missing,
                    departure_reason=departure_reason,
                    arrival_reason=arrival_reason,
                    canceled_departure=canceled_departure,
                    canceled_arrival=canceled_arrival,
                    canceled=canceled_any,
                )
            )

    dedup: dict[tuple[str, str], Observation] = {}
    for row in observations:
        dedup[(row.route_label, row.train_id)] = row

    return sorted(dedup.values(), key=lambda x: (x.route_label, x.planned_departure, x.train_name))
