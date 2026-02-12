from __future__ import annotations

import sqlite3
from pathlib import Path

from db_monitor.models import CarObservation, Observation


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_ts TEXT NOT NULL,
    service_date TEXT NOT NULL,
    train_id TEXT NOT NULL,
    train_name TEXT,
    line TEXT,
    route_label TEXT NOT NULL,
    source_station TEXT NOT NULL,
    target_station TEXT NOT NULL,
    planned_departure TEXT NOT NULL,
    actual_departure TEXT,
    planned_arrival TEXT,
    actual_arrival TEXT,
    delay_minutes INTEGER NOT NULL,
    schedule_deviation_minutes INTEGER NOT NULL,
    arrival_delay_minutes INTEGER NOT NULL DEFAULT 0,
    arrival_schedule_deviation_minutes INTEGER NOT NULL DEFAULT 0,
    arrival_observed INTEGER NOT NULL DEFAULT 0,
    arrival_info_missing INTEGER NOT NULL DEFAULT 0,
    departure_reason TEXT,
    arrival_reason TEXT,
    canceled_departure INTEGER NOT NULL DEFAULT 0,
    canceled_arrival INTEGER NOT NULL DEFAULT 0,
    canceled INTEGER NOT NULL,
    UNIQUE(service_date, train_id, route_label)
);

CREATE TABLE IF NOT EXISTS car_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_ts TEXT NOT NULL,
    service_date TEXT NOT NULL,
    route_label TEXT NOT NULL,
    from_name TEXT NOT NULL,
    to_name TEXT NOT NULL,
    target_departure_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    distance_km REAL NOT NULL,
    UNIQUE(service_date, route_label, target_departure_time)
);
"""


REQUIRED_COLUMNS: dict[str, str] = {
    "train_name": "TEXT",
    "arrival_delay_minutes": "INTEGER NOT NULL DEFAULT 0",
    "arrival_schedule_deviation_minutes": "INTEGER NOT NULL DEFAULT 0",
    "arrival_observed": "INTEGER NOT NULL DEFAULT 0",
    "arrival_info_missing": "INTEGER NOT NULL DEFAULT 0",
    "departure_reason": "TEXT",
    "arrival_reason": "TEXT",
    "canceled_departure": "INTEGER NOT NULL DEFAULT 0",
    "canceled_arrival": "INTEGER NOT NULL DEFAULT 0",
}


class ObservationStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with sqlite3.connect(self.db_path) as con:
            con.executescript(SCHEMA)
            self._migrate_existing_table(con)
            con.commit()

    @staticmethod
    def _migrate_existing_table(con: sqlite3.Connection) -> None:
        existing = {
            row[1]
            for row in con.execute("PRAGMA table_info(observations)").fetchall()
        }
        for column, column_type in REQUIRED_COLUMNS.items():
            if column not in existing:
                con.execute(f"ALTER TABLE observations ADD COLUMN {column} {column_type}")

    def upsert_many(self, rows: list[Observation]) -> int:
        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as con:
            cursor = con.cursor()
            cursor.executemany(
                """
                INSERT INTO observations (
                    observation_ts, service_date, train_id, train_name, line, route_label,
                    source_station, target_station, planned_departure, actual_departure,
                    planned_arrival, actual_arrival, delay_minutes, schedule_deviation_minutes,
                    arrival_delay_minutes, arrival_schedule_deviation_minutes, arrival_observed, arrival_info_missing,
                    departure_reason, arrival_reason,
                    canceled_departure, canceled_arrival, canceled
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_date, train_id, route_label)
                DO UPDATE SET
                    observation_ts=excluded.observation_ts,
                    train_name=excluded.train_name,
                    line=excluded.line,
                    source_station=excluded.source_station,
                    target_station=excluded.target_station,
                    planned_departure=excluded.planned_departure,
                    actual_departure=excluded.actual_departure,
                    planned_arrival=excluded.planned_arrival,
                    actual_arrival=CASE
                        WHEN excluded.arrival_observed = 1 THEN excluded.actual_arrival
                        ELSE observations.actual_arrival
                    END,
                    delay_minutes=excluded.delay_minutes,
                    schedule_deviation_minutes=excluded.schedule_deviation_minutes,
                    arrival_delay_minutes=CASE
                        WHEN excluded.arrival_observed = 1 THEN excluded.arrival_delay_minutes
                        ELSE observations.arrival_delay_minutes
                    END,
                    arrival_schedule_deviation_minutes=CASE
                        WHEN excluded.arrival_observed = 1 THEN excluded.arrival_schedule_deviation_minutes
                        ELSE observations.arrival_schedule_deviation_minutes
                    END,
                    arrival_observed=CASE
                        WHEN observations.arrival_observed = 1 OR excluded.arrival_observed = 1 THEN 1
                        ELSE 0
                    END,
                    arrival_info_missing=CASE
                        WHEN observations.arrival_observed = 1 OR excluded.arrival_observed = 1 THEN 0
                        WHEN observations.arrival_info_missing = 1 OR excluded.arrival_info_missing = 1 THEN 1
                        ELSE 0
                    END,
                    departure_reason=excluded.departure_reason,
                    arrival_reason=CASE
                        WHEN excluded.arrival_observed = 1 THEN excluded.arrival_reason
                        WHEN observations.arrival_reason IS NOT NULL AND observations.arrival_reason != '' THEN observations.arrival_reason
                        ELSE excluded.arrival_reason
                    END,
                    canceled_departure=excluded.canceled_departure,
                    canceled_arrival=CASE
                        WHEN excluded.arrival_observed = 1 THEN excluded.canceled_arrival
                        ELSE observations.canceled_arrival
                    END,
                    canceled=excluded.canceled
                """,
                [
                    (
                        row.observation_ts.isoformat(),
                        row.service_date,
                        row.train_id,
                        row.train_name,
                        row.line,
                        row.route_label,
                        row.source_station,
                        row.target_station,
                        row.planned_departure.isoformat(),
                        row.actual_departure.isoformat() if row.actual_departure else None,
                        row.planned_arrival.isoformat() if row.planned_arrival else None,
                        row.actual_arrival.isoformat() if row.actual_arrival else None,
                        row.delay_minutes,
                        row.schedule_deviation_minutes,
                        row.arrival_delay_minutes,
                        row.arrival_schedule_deviation_minutes,
                        int(row.arrival_observed),
                        int(row.arrival_info_missing),
                        row.departure_reason,
                        row.arrival_reason,
                        int(row.canceled_departure),
                        int(row.canceled_arrival),
                        int(row.canceled),
                    )
                    for row in rows
                ],
            )
            con.commit()
        return len(rows)

    def upsert_car_many(self, rows: list[CarObservation]) -> int:
        if not rows:
            return 0

        with sqlite3.connect(self.db_path) as con:
            cursor = con.cursor()
            cursor.executemany(
                """
                INSERT INTO car_observations (
                    observation_ts, service_date, route_label, from_name, to_name,
                    target_departure_time, duration_minutes, distance_km
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_date, route_label, target_departure_time)
                DO UPDATE SET
                    observation_ts=excluded.observation_ts,
                    from_name=excluded.from_name,
                    to_name=excluded.to_name,
                    duration_minutes=excluded.duration_minutes,
                    distance_km=excluded.distance_km
                """,
                [
                    (
                        row.observation_ts.isoformat(),
                        row.service_date,
                        row.route_label,
                        row.from_name,
                        row.to_name,
                        row.target_departure_time,
                        row.duration_minutes,
                        row.distance_km,
                    )
                    for row in rows
                ],
            )
            con.commit()
        return len(rows)
