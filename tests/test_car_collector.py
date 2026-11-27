from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from db_monitor.car_collector import collect_car_observations
from db_monitor.config import CarRoute, Settings
from db_monitor.models import CarObservation
from db_monitor.storage import ObservationStore

TZ = ZoneInfo("Europe/Berlin")
SETTINGS = Settings(
    client_id="t", api_key="t", timezone="Europe/Berlin",
    station_endpoint="https://example.test/s", timetables_endpoint="https://example.test/t",
    openboards_endpoint="https://example.test/o", db_api_provider="openboards",
    database_path=":memory:", car_provider="tomtom", ors_api_key="",
    ors_directions_endpoint="https://example.test/ors", tomtom_api_key="key",
    tomtom_routing_endpoint="https://example.test/tomtom",
)
MORNING = CarRoute(
    label="Car Morning Freiburg->Offenburg", from_name="A", to_name="B",
    from_lat=48.0, from_lon=7.8, to_lat=48.5, to_lon=7.9, target_departure=time(6, 30),
)


class CarCollectorTests(unittest.TestCase):
    def _collect(self, now: datetime, existing=None):
        calls = []

        def fake_fetch(_settings, _route, depart_at=None):
            calls.append(depart_at)
            return 42, 65.5

        with patch("db_monitor.car_collector._fetch_route_duration", side_effect=fake_fetch):
            rows = collect_car_observations(SETTINGS, [MORNING], existing, now=now)
        return rows, calls

    def test_live_inside_window(self) -> None:
        rows, calls = self._collect(datetime(2026, 11, 27, 6, 50, tzinfo=TZ))
        self.assertEqual(calls, [None])
        self.assertEqual((rows[0].source, rows[0].service_date), ("live", "2026-11-27"))

    def test_forecast_for_today_before_window(self) -> None:
        rows, calls = self._collect(datetime(2026, 11, 27, 1, 49, tzinfo=TZ))
        self.assertEqual(calls, [datetime(2026, 11, 27, 6, 30, tzinfo=TZ)])
        self.assertEqual((rows[0].source, rows[0].service_date), ("forecast", "2026-11-27"))

    def test_forecast_for_tomorrow_after_window(self) -> None:
        rows, calls = self._collect(datetime(2026, 11, 26, 18, 47, tzinfo=TZ))
        self.assertEqual(calls, [datetime(2026, 11, 27, 6, 30, tzinfo=TZ)])
        self.assertEqual((rows[0].source, rows[0].service_date), ("forecast", "2026-11-27"))

    def test_late_run_forecasts_instead_of_measuring_off_peak(self) -> None:
        # 07:40 is 70 min after the commute time: live traffic no longer reflects it.
        rows, calls = self._collect(datetime(2026, 11, 26, 7, 40, tzinfo=TZ))
        self.assertEqual(calls, [datetime(2026, 11, 27, 6, 30, tzinfo=TZ)])
        self.assertEqual(rows[0].source, "forecast")

    def test_skips_weekend_commutes(self) -> None:
        # Friday evening: the next departure would be Saturday 06:30.
        rows, calls = self._collect(datetime(2026, 11, 27, 18, 47, tzinfo=TZ))
        friday_evening_rows = rows
        self.assertEqual(friday_evening_rows, [])

    def test_skips_live_when_already_measured(self) -> None:
        rows, calls = self._collect(
            datetime(2026, 11, 27, 6, 50, tzinfo=TZ), existing={(MORNING.label, "06:30")}
        )
        self.assertEqual((rows, calls), ([], []))


class CarStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.store = ObservationStore(str(Path(self._tmp.name) / "t.db"))
        self.store.initialize()
        self.base = CarObservation(
            observation_ts=datetime(2026, 11, 27, 1, 0, tzinfo=TZ), service_date="2026-11-27",
            route_label=MORNING.label, from_name="A", to_name="B", target_departure_time="06:30",
            duration_minutes=40, distance_km=65.5, source="forecast",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _stored(self) -> tuple[int, str]:
        with sqlite3.connect(self.store.db_path) as con:
            return con.execute("SELECT duration_minutes, source FROM car_observations").fetchone()

    def test_live_replaces_forecast_but_not_vice_versa(self) -> None:
        self.store.upsert_car_many([self.base])
        live = replace(self.base, observation_ts=datetime(2026, 11, 27, 7, 0, tzinfo=TZ),
                       duration_minutes=55, source="live")
        self.store.upsert_car_many([live])
        self.assertEqual(self._stored(), (55, "live"))
        later_forecast = replace(self.base, observation_ts=datetime(2026, 11, 27, 9, 0, tzinfo=TZ),
                                 duration_minutes=41)
        self.store.upsert_car_many([later_forecast])
        self.assertEqual(self._stored(), (55, "live"))
        self.assertEqual(self.store.existing_car_targets_for_date("2026-11-27"), {(MORNING.label, "06:30")})

    def test_newer_forecast_replaces_older_and_does_not_block_live(self) -> None:
        self.store.upsert_car_many([self.base])
        self.store.upsert_car_many([replace(self.base, duration_minutes=44,
                                            observation_ts=datetime(2026, 11, 27, 2, 0, tzinfo=TZ))])
        self.assertEqual(self._stored(), (44, "forecast"))
        self.assertEqual(self.store.existing_car_targets_for_date("2026-11-27"), set())

    def test_migrates_old_table_without_source(self) -> None:
        old = ObservationStore(str(Path(self._tmp.name) / "old.db"))
        with sqlite3.connect(old.db_path) as con:
            con.execute("""CREATE TABLE car_observations (id INTEGER PRIMARY KEY, observation_ts TEXT,
                service_date TEXT, route_label TEXT, from_name TEXT, to_name TEXT,
                target_departure_time TEXT, duration_minutes INTEGER, distance_km REAL,
                UNIQUE(service_date, route_label, target_departure_time))""")
            con.execute("INSERT INTO car_observations VALUES (1,'x','2026-08-27','r','A','B','16:30',50,66.1)")
        old.initialize()
        with sqlite3.connect(old.db_path) as con:
            self.assertEqual(con.execute("SELECT source FROM car_observations").fetchone(), ("live",))


if __name__ == "__main__":
    unittest.main()
