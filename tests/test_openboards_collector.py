from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from db_monitor.client import DBApiClient
from db_monitor.config import RouteWindow, Settings
from db_monitor.models import Observation
from db_monitor.openboards_collector import collect_openboards_observations
from db_monitor.storage import ObservationStore


class OpenBoardsCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = ZoneInfo("Europe/Berlin")
        now = datetime.now(self.tz).replace(second=0, microsecond=0)
        self.departure = now - timedelta(minutes=2)
        self.arrival = now + timedelta(minutes=20)
        self.window = RouteWindow(
            label="Afternoon Offenburg->Freiburg",
            source_station="Offenburg",
            target_station="Freiburg(Breisgau) Hbf",
            source_eva="8000290",
            target_eva="8000107",
            start_time=(now - timedelta(minutes=5)).time(),
            end_time=(now + timedelta(minutes=30)).time(),
        )
        self.settings = Settings(
            client_id="test",
            api_key="test",
            timezone="Europe/Berlin",
            station_endpoint="https://example.test/stations",
            timetables_endpoint="https://example.test/timetables",
            openboards_endpoint="https://example.test/open-boards",
            db_api_provider="openboards",
            database_path=":memory:",
            car_provider="ors",
            ors_api_key="",
            ors_directions_endpoint="https://example.test/ors",
            tomtom_api_key="",
            tomtom_routing_endpoint="https://example.test/tomtom",
        )

    def _entry(self, *, board: str, identity: str, time_type: str, when: datetime, via: str) -> dict:
        return {
            "journeyId": identity,
            "timeSchedule": when.isoformat(),
            "time": when.isoformat(),
            "timeType": time_type,
            "transport": {
                "category": "ICE",
                "journeyNumber": "73",
                "via": [{"evaNumber": via}],
            },
            "board": board,
        }

    def test_filters_route_and_matches_arrival_by_journey_id(self) -> None:
        departure = self._entry(
            board="departure", identity="journey-1", time_type="REALTIME",
            when=self.departure, via="8000107",
        )
        unrelated = self._entry(
            board="departure", identity="journey-unrelated", time_type="REALTIME",
            when=self.departure, via="9999999",
        )
        arrival = self._entry(
            board="arrival", identity="journey-1", time_type="FORECAST",
            when=self.arrival, via="8000290",
        )

        def board_response(_client, board, _eva, _start, _end):
            return [departure, unrelated] if board == "departures" else [arrival]

        with patch("db_monitor.openboards_collector._fetch_board_window", side_effect=board_response):
            rows = collect_openboards_observations(
                self.settings,
                [self.window],
                allowed_train_names_by_route={self.window.label: {"UNSEEN_TRAIN"}},
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.train_name, "ICE73")
        self.assertEqual(row.train_id, "journey-1")
        self.assertEqual(row.planned_arrival, self.arrival.replace(tzinfo=None))
        self.assertFalse(row.arrival_observed)

    def test_marks_explicit_cancelled_arrival_as_observed(self) -> None:
        departure = self._entry(
            board="departure", identity="journey-2", time_type="SCHEDULE",
            when=self.departure, via="8000107",
        )
        arrival = self._entry(
            board="arrival", identity="journey-2", time_type="SCHEDULE",
            when=self.arrival, via="8000290",
        )
        arrival["cancelled"] = True

        def board_response(_client, board, _eva, _start, _end):
            return [departure] if board == "departures" else [arrival]

        with patch("db_monitor.openboards_collector._fetch_board_window", side_effect=board_response):
            rows = collect_openboards_observations(self.settings, [self.window])

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].arrival_observed)
        self.assertTrue(rows[0].canceled_arrival)
        self.assertIsNone(rows[0].actual_arrival)
        self.assertEqual(rows[0].arrival_reason, "Ausfall")

    def test_infers_on_time_preview_only_after_scheduled_arrival(self) -> None:
        now = datetime.now(self.tz).replace(second=0, microsecond=0)
        past = now - timedelta(minutes=2)
        departure = self._entry(
            board="departure", identity="journey-3", time_type="PREVIEW",
            when=past, via="8000107",
        )
        arrival = self._entry(
            board="arrival", identity="journey-3", time_type="PREVIEW",
            when=past, via="8000290",
        )

        def board_response(_client, board, _eva, _start, _end):
            return [departure] if board == "departures" else [arrival]

        with patch("db_monitor.openboards_collector._fetch_board_window", side_effect=board_response):
            rows = collect_openboards_observations(self.settings, [self.window])

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].arrival_observed)
        self.assertEqual(rows[0].actual_arrival, past.replace(tzinfo=None))

    def test_uses_db_real_time_type_for_actual_delay(self) -> None:
        now = datetime.now(self.tz).replace(second=0, microsecond=0)
        dep_plan = now - timedelta(minutes=60)
        dep_actual = dep_plan + timedelta(minutes=6)
        arr_plan = now - timedelta(minutes=30)
        arr_actual = arr_plan + timedelta(minutes=10)
        departure = self._entry(
            board="departure", identity="journey-real", time_type="REAL",
            when=dep_plan, via="8000107",
        )
        departure["time"] = dep_actual.isoformat()
        arrival = self._entry(
            board="arrival", identity="journey-real", time_type="REAL",
            when=arr_plan, via="8000290",
        )
        arrival["time"] = arr_actual.isoformat()
        arrival["journeyInfo"] = {
            "destination": {"evaNumber": "8098160", "cancelled": True}
        }
        arrival["messages"] = [10]
        arrival["_rootMessages"] = {
            "attributes": [
                {"messageID": 10, "descriptions": [{"language": "deu", "text": "Nicht als Grund verwenden"}]}
            ],
            "risCauseCodes": [
                {"messageID": 10, "descriptions": [{"language": "deu", "text": "Verspätung eines vorausfahrenden Zuges"}]}
            ],
        }

        def board_response(_client, board, _eva, _start, _end):
            return [departure] if board == "departures" else [arrival]

        with patch("db_monitor.openboards_collector._fetch_board_window", side_effect=board_response):
            rows = collect_openboards_observations(self.settings, [self.window])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].schedule_deviation_minutes, 6)
        self.assertEqual(rows[0].arrival_schedule_deviation_minutes, 10)
        self.assertTrue(rows[0].arrival_observed)
        self.assertFalse(rows[0].canceled_arrival)
        self.assertEqual(rows[0].actual_arrival, arr_actual.replace(tzinfo=None))
        self.assertEqual(rows[0].arrival_reason, "Verspätung eines vorausfahrenden Zuges")

    def test_reuses_existing_ids_when_provider_changes_midday(self) -> None:
        now = datetime.now(self.tz)
        row = Observation(
            observation_ts=now,
            service_date=now.date().isoformat(),
            train_id="timetables-id",
            train_name="ICE73",
            line="ICE73",
            route_label="Afternoon Offenburg->Freiburg",
            source_station="Offenburg",
            target_station="Freiburg(Breisgau) Hbf",
            planned_departure=now.replace(tzinfo=None),
            actual_departure=None,
            planned_arrival=None,
            actual_arrival=None,
            delay_minutes=0,
            schedule_deviation_minutes=0,
            arrival_delay_minutes=0,
            arrival_schedule_deviation_minutes=0,
            arrival_observed=False,
            arrival_info_missing=False,
            departure_reason="",
            arrival_reason="",
            canceled_departure=False,
            canceled_arrival=False,
            canceled=False,
        )
        with TemporaryDirectory() as tmp:
            store = ObservationStore(f"{tmp}/test.db")
            store.initialize()
            store.upsert_many([row])
            switched = Observation(**{**row.__dict__, "train_id": "openboards-id"})
            mapped = store.reuse_existing_train_ids([switched])

        self.assertEqual(mapped[0].train_id, "timetables-id")

    def test_openboards_client_sends_documented_board_payload(self) -> None:
        client = DBApiClient(self.settings)
        response = Mock()
        response.status_code = 200
        response.text = "{}"
        response.json.return_value = {"departures": []}
        with patch.object(client.session, "post", return_value=response) as post:
            result = client.get_open_board(
                "departures", "8000290", self.departure.replace(tzinfo=self.tz),
                self.arrival.replace(tzinfo=self.tz),
            )

        self.assertEqual(result, {"departures": []})
        self.assertEqual(post.call_args.args[0], "https://example.test/open-boards/departures")
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["stopPlaceOrStation"]["evaNumbers"], ["8000290"])
        self.assertEqual(sent["timeWindow"]["timeStart"], self.departure.replace(tzinfo=self.tz).isoformat())
        self.assertFalse(sent["noCancellations"])
        self.assertEqual(sent["maxViaStops"], 10)


if __name__ == "__main__":
    unittest.main()
