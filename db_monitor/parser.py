from __future__ import annotations

from datetime import datetime, time
from xml.etree import ElementTree as ET

from db_monitor.models import ChangeInfo, PlannedStop


def parse_db_time(raw: str) -> datetime:
    value = raw.strip()
    if len(value) < 10:
        raise ValueError(f"Invalid DB timestamp: {raw}")
    return datetime.strptime(value[:10], "%y%m%d%H%M")


def _extract_path(stop: ET.Element) -> str:
    dp = stop.find("dp")
    if dp is not None and dp.get("ppth"):
        return dp.get("ppth", "")
    ar = stop.find("ar")
    if ar is not None and ar.get("ppth"):
        return ar.get("ppth", "")
    return ""


def _extract_train_name(stop: ET.Element) -> tuple[str, str]:
    tl = stop.find("tl")
    if tl is None:
        return "Unbekannt", ""

    category = (tl.get("c") or "").strip()
    number = (tl.get("n") or "").strip()

    if category and number:
        compact = f"{category}{number}".replace(" ", "")
        return compact, compact
    if number:
        return number, number
    if category:
        return category, category

    fallback = ((tl.get("o") or "").strip() or "Unbekannt").replace(" ", "")
    return fallback, fallback


def _extract_reasons(stop: ET.Element, node: ET.Element | None) -> str:
    raw: list[str] = []

    # Messages can appear on stop level and event level (dp/ar).
    scope_nodes = [stop]
    if node is not None:
        scope_nodes.append(node)

    for scope in scope_nodes:
        for msg in scope.findall("m"):
            parts: list[str] = []
            for key in ("t", "txt", "cat", "c", "from", "to", "id"):
                value = (msg.get(key) or "").strip()
                if value:
                    parts.append(value)
            text = " ".join(parts).strip()
            if not text:
                text = (msg.text or "").strip()
            if text:
                raw.append(text)

    # Keep first occurrence order and remove duplicates.
    dedup: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item in seen:
            continue
        seen.add(item)
        dedup.append(item)
    return " | ".join(dedup)


def _parse_plan_generic(
    xml_payload: str,
    source_station: str,
    target_station: str,
    route_label: str,
    window_start: time,
    window_end: time,
    mode: str,
    required_in_path: str,
) -> list[PlannedStop]:
    root = ET.fromstring(xml_payload)
    rows: list[PlannedStop] = []

    for stop in root.findall("s"):
        train_id = stop.get("id")
        if not train_id:
            continue

        train_name, line = _extract_train_name(stop)
        dp = stop.find("dp")
        ar = stop.find("ar")

        planned_departure = parse_db_time(dp.get("pt", "")) if dp is not None and dp.get("pt") else None
        planned_arrival = parse_db_time(ar.get("pt", "")) if ar is not None and ar.get("pt") else None

        event_time = planned_departure if mode == "departure" else planned_arrival
        if event_time is None:
            continue
        if event_time.time() < window_start or event_time.time() > window_end:
            continue

        path_raw = _extract_path(stop)
        if path_raw:
            path_stations = [x.strip().lower() for x in path_raw.split("|") if x.strip()]
            if required_in_path.lower() not in path_stations:
                continue

        rows.append(
            PlannedStop(
                train_id=train_id,
                train_name=train_name,
                line=line,
                source_station=source_station,
                target_station=target_station,
                planned_departure=planned_departure,
                planned_arrival=planned_arrival,
                route_label=route_label,
            )
        )

    return rows


def parse_departures_plan(
    xml_payload: str,
    source_station: str,
    target_station: str,
    route_label: str,
    window_start: time,
    window_end: time,
) -> list[PlannedStop]:
    return _parse_plan_generic(
        xml_payload=xml_payload,
        source_station=source_station,
        target_station=target_station,
        route_label=route_label,
        window_start=window_start,
        window_end=window_end,
        mode="departure",
        required_in_path=target_station,
    )


def parse_arrivals_plan(
    xml_payload: str,
    source_station: str,
    target_station: str,
    route_label: str,
    window_start: time,
    window_end: time,
) -> list[PlannedStop]:
    return _parse_plan_generic(
        xml_payload=xml_payload,
        source_station=source_station,
        target_station=target_station,
        route_label=route_label,
        window_start=window_start,
        window_end=window_end,
        mode="arrival",
        required_in_path=source_station,
    )


def parse_changes(xml_payload: str) -> dict[str, ChangeInfo]:
    root = ET.fromstring(xml_payload)
    result: dict[str, ChangeInfo] = {}

    for stop in root.findall("s"):
        train_id = stop.get("id")
        if not train_id:
            continue

        dp = stop.find("dp")
        ar = stop.find("ar")

        changed_departure = None
        if dp is not None and dp.get("ct"):
            changed_departure = parse_db_time(dp.get("ct", ""))

        changed_arrival = None
        if ar is not None and ar.get("ct"):
            changed_arrival = parse_db_time(ar.get("ct", ""))

        departure_reason = _extract_reasons(stop, dp)
        arrival_reason = _extract_reasons(stop, ar)

        canceled = False
        if dp is not None and dp.get("cs") == "c":
            canceled = True
        if ar is not None and ar.get("cs") == "c":
            canceled = True

        result[train_id] = ChangeInfo(
            train_id=train_id,
            changed_departure=changed_departure,
            changed_arrival=changed_arrival,
            departure_reason=departure_reason,
            arrival_reason=arrival_reason,
            canceled=canceled,
        )

    return result
