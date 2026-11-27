from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from db_monitor.client import DBApiClient
from db_monitor.config import RouteWindow, Settings
from db_monitor.models import Observation


REALTIME_TYPES = {"REAL", "REALTIME", "REAL_TIME", "ACTUAL", "ECHT"}
SCHEDULE_TYPES = {"SCHEDULE", "SCHEDULED", "PLAN"}


def _local_datetime(value: Any, timezone: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
    return parsed


def _contains_eva(value: Any, eva: str) -> bool:
    if isinstance(value, dict):
        if str(value.get("evaNumber", "")) == str(eva):
            return True
        return any(_contains_eva(item, eva) for item in value.values())
    if isinstance(value, list):
        return any(_contains_eva(item, eva) for item in value)
    return False


def _has_cancel_flag(value: dict[str, Any]) -> bool:
    true_fields = {"canceled", "cancelled", "iscanceled", "iscancelled", "stopcancelled", "stopcanceled"}
    for key, field_value in value.items():
        normalized = key.replace("_", "").lower()
        if normalized in true_fields and field_value is True:
            return True
        if normalized in {"status", "timetype"} and isinstance(field_value, str):
            if field_value.upper() in {"CANCELLED", "CANCELED", "AUSFALL"}:
                return True
    return False


def _is_canceled(entry: dict[str, Any], eva: str) -> bool:
    # Do not recursively treat a cancellation at the train's final destination
    # as a cancellation of this intermediate stop (the API reports both).
    if _has_cancel_flag(entry):
        return True

    journey_info = entry.get("journeyInfo")
    if isinstance(journey_info, dict):
        for endpoint in ("origin", "destination"):
            stop = journey_info.get(endpoint)
            if isinstance(stop, dict) and str(stop.get("evaNumber", "")) == str(eva):
                if _has_cancel_flag(stop):
                    return True

    transport = entry.get("transport")
    if isinstance(transport, dict):
        for key in ("via", "stops", "cancelledStops", "canceledStops"):
            stops = transport.get(key)
            if isinstance(stops, list):
                for stop in stops:
                    if (
                        isinstance(stop, dict)
                        and str(stop.get("evaNumber", "")) == str(eva)
                        and _has_cancel_flag(stop)
                    ):
                        return True
    return False


def _entry_identity(entry: dict[str, Any]) -> str:
    journey_id = entry.get("journeyId")
    if journey_id:
        return str(journey_id)
    return str(entry.get("departureId") or entry.get("arrivalId") or "")


def _train_fields(entry: dict[str, Any]) -> tuple[str, str]:
    transport = entry.get("transport") or {}
    category = str(transport.get("category") or "").strip()
    number = str(transport.get("journeyNumber") or "").strip()
    name = f"{category}{number}".replace(" ", "") or "Unbekannt"
    line_value = transport.get("line")
    if isinstance(line_value, dict):
        line = str(line_value.get("name") or line_value.get("label") or "")
    else:
        line = str(line_value or "")
    return name, line


def _entries(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key, [])
    root_messages = payload.get("messages") or {}
    result: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                item = dict(item)
                item["_rootMessages"] = root_messages
                result.append(item)
    return result


def _entry_reasons(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    refs = entry.get("messages")
    if not isinstance(refs, list):
        return ""
    wanted = {str(ref) for ref in refs}
    root_messages = entry.get("_rootMessages") or {}
    if not isinstance(root_messages, dict):
        return ""

    texts: list[str] = []
    for group in ("disruptions", "notes", "risCauseCodes", "risQualityDeviations"):
        items = root_messages.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or str(item.get("messageID", "")) not in wanted:
                continue
            descriptions = item.get("descriptions")
            if isinstance(descriptions, list):
                localized = next(
                    (
                        description.get("text")
                        for description in descriptions
                        if isinstance(description, dict)
                        and description.get("language") == "deu"
                        and description.get("text")
                    ),
                    None,
                )
                if localized:
                    texts.append(str(localized).strip())

    return " | ".join(dict.fromkeys(text for text in texts if text))


def _fetch_board_window(
    client: DBApiClient,
    board: str,
    eva: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    unidentified: list[dict[str, Any]] = []
    cursor = start
    # Keep individual requests to two hours: it fits the boards API window and
    # avoids silently truncated results on the afternoon arrival lookahead.
    while cursor < end:
        chunk_end = min(cursor + timedelta(hours=2), end)
        payload = client.get_open_board(board, eva, cursor, chunk_end)
        for entry in _entries(payload, board):
            # Adjacent chunks can return the same journey twice; keep the latest.
            identity = _entry_identity(entry)
            if identity:
                results[identity] = entry
            else:
                unidentified.append(entry)
        cursor = chunk_end
    return [*results.values(), *unidentified]


def _in_window(planned: datetime | None, service_date: date, window: RouteWindow) -> bool:
    if planned is None or planned.date() != service_date:
        return False
    return window.start_time <= planned.time() <= window.end_time


def collect_openboards_observations(
    settings: Settings,
    windows: list[RouteWindow],
    allowed_train_names_by_route: dict[str, set[str]] | None = None,
    fallback_departure_times_by_route_train: dict[tuple[str, str], time] | None = None,
    service_date: date | None = None,
) -> list[Observation]:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    now_naive = now.replace(tzinfo=None)
    if service_date is None:
        service_date = now.date()
    client = DBApiClient(settings)
    observations: list[Observation] = []

    for window in windows:
        dep_start = datetime.combine(service_date, window.start_time).replace(tzinfo=tz)
        dep_end = datetime.combine(service_date, window.end_time).replace(tzinfo=tz)
        # The boards API filters by the *current* (real-time) event time. Query
        # well past the window so heavily delayed trains are still returned, then
        # filter on the planned time below.
        departures = _fetch_board_window(
            client, "departures", window.source_eva, dep_start, dep_end + timedelta(hours=3)
        )
        arrivals = _fetch_board_window(
            client, "arrivals", window.target_eva, dep_start, dep_end + timedelta(hours=5)
        )

        # Direction is proven by the same journey appearing on the target
        # station's arrival board after leaving the source station. The via list
        # is capped (maxViaStops), so regional trains with many intermediate stops
        # often omit the target; it is only used as a fallback.
        arrivals_by_id = {
            _entry_identity(item): item for item in arrivals if _entry_identity(item)
        }

        for dep in departures:
            journey_id = _entry_identity(dep)
            if not journey_id:
                continue
            train_name, line = _train_fields(dep)
            planned_departure = _local_datetime(dep.get("timeSchedule"), settings.timezone)
            if not _in_window(planned_departure, service_date, window):
                continue

            arr = arrivals_by_id.get(journey_id)
            planned_arrival_check = (
                _local_datetime(arr.get("timeSchedule"), settings.timezone) if arr else None
            )
            if arr is not None and (
                planned_arrival_check is None or planned_arrival_check < planned_departure
            ):
                arr = None
            if arr is None and not _contains_eva(
                {
                    "via": (dep.get("transport") or {}).get("via", []),
                    "destination": (dep.get("journeyInfo") or {}).get("destination"),
                },
                window.target_eva,
            ):
                continue
            actual_dep_time = _local_datetime(dep.get("time"), settings.timezone)
            dep_type = str(dep.get("timeType") or "").upper()
            dep_realtime = dep_type in REALTIME_TYPES and actual_dep_time is not None
            canceled_departure = _is_canceled(dep, window.source_eva)
            dep_schedule_observed = bool(
                dep_type in SCHEDULE_TYPES | {"PREVIEW"}
                and actual_dep_time == planned_departure
                and planned_departure <= now_naive
                and now_naive <= planned_departure + timedelta(hours=1)
            )
            actual_departure = (
                actual_dep_time if dep_realtime
                else planned_departure if dep_schedule_observed
                else None
            )
            dep_deviation = (
                int((actual_departure - planned_departure).total_seconds() / 60)
                if actual_departure else 0
            )

            planned_arrival = _local_datetime(arr.get("timeSchedule"), settings.timezone) if arr else None
            actual_arr_time = _local_datetime(arr.get("time"), settings.timezone) if arr else None
            arr_type = str(arr.get("timeType") or "").upper() if arr else ""
            arr_realtime = bool(arr and arr_type in REALTIME_TYPES and actual_arr_time)
            canceled_arrival = _is_canceled(arr, window.target_eva) if arr else False
            arrival_deadline = planned_arrival + timedelta(hours=1) if planned_arrival else None
            arrival_observed = bool(
                arr and (
                    canceled_arrival
                    or (
                        arr_realtime and actual_arr_time and actual_arr_time <= now_naive
                    )
                    or (
                        planned_arrival and now_naive >= planned_arrival
                        and arrival_deadline and now_naive <= arrival_deadline
                        and (
                            arr_type in SCHEDULE_TYPES
                            or (
                                arr_type == "PREVIEW"
                                and actual_arr_time == planned_arrival
                            )
                        )
                    )
                )
            )
            actual_arrival = None
            arr_deviation = 0
            if arrival_observed and planned_arrival:
                actual_arrival = (
                    None if canceled_arrival
                    else actual_arr_time if arr_realtime
                    else planned_arrival
                )
                if actual_arrival is not None:
                    arr_deviation = int((actual_arrival - planned_arrival).total_seconds() / 60)
            arrival_info_missing = bool(
                planned_arrival and arrival_deadline and now_naive > arrival_deadline
                and not arrival_observed
            )
            dep_reason = "Ausfall" if canceled_departure else _entry_reasons(dep)
            arr_reason = "Ausfall" if canceled_arrival else _entry_reasons(arr)
            observations.append(
                Observation(
                    observation_ts=now,
                    service_date=service_date.isoformat(),
                    train_id=journey_id,
                    train_name=train_name,
                    line=line,
                    route_label=window.label,
                    source_station=window.source_station,
                    target_station=window.target_station,
                    planned_departure=planned_departure,
                    actual_departure=actual_departure,
                    planned_arrival=planned_arrival,
                    actual_arrival=actual_arrival,
                    delay_minutes=max(0, dep_deviation),
                    schedule_deviation_minutes=dep_deviation,
                    arrival_delay_minutes=max(0, arr_deviation),
                    arrival_schedule_deviation_minutes=arr_deviation,
                    arrival_observed=arrival_observed,
                    arrival_info_missing=arrival_info_missing,
                    departure_reason=dep_reason,
                    arrival_reason=arr_reason,
                    canceled_departure=canceled_departure,
                    canceled_arrival=canceled_arrival,
                    canceled=canceled_departure or canceled_arrival,
                )
            )

    dedup: dict[tuple[str, str], Observation] = {}
    for row in observations:
        dedup[(row.route_label, row.train_id)] = row
    return sorted(dedup.values(), key=lambda row: (row.route_label, row.planned_departure, row.train_name))
