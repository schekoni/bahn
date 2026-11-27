from __future__ import annotations

import unittest
from datetime import date, datetime, time
from types import SimpleNamespace

from db_monitor.config import RouteWindow
from run_collection import collection_dates, missing_route_problems


MORNING = RouteWindow(
    label="Morning Freiburg->Offenburg",
    source_station="Freiburg(Breisgau) Hbf",
    target_station="Offenburg",
    source_eva="8000107",
    target_eva="8000290",
    start_time=time(6, 0),
    end_time=time(8, 0),
)
AFTERNOON = RouteWindow(
    label="Afternoon Offenburg->Freiburg",
    source_station="Offenburg",
    target_station="Freiburg(Breisgau) Hbf",
    source_eva="8000290",
    target_eva="8000107",
    start_time=time(15, 30),
    end_time=time(17, 30),
)


class CollectionDatesTests(unittest.TestCase):
    def test_daytime_run_backfills_yesterday_and_collects_today(self) -> None:
        self.assertEqual(
            collection_dates(date(2026, 11, 27), early_hours_run=False, backfill_days=1),
            [date(2026, 11, 26), date(2026, 11, 27)],
        )

    def test_early_hours_run_skips_today(self) -> None:
        self.assertEqual(
            collection_dates(date(2026, 11, 28), early_hours_run=True, backfill_days=1),
            [date(2026, 11, 27)],
        )

    def test_early_hours_run_without_backfill_still_finishes_yesterday(self) -> None:
        self.assertEqual(
            collection_dates(date(2026, 11, 28), early_hours_run=True, backfill_days=0),
            [date(2026, 11, 27)],
        )


class MissingRouteTests(unittest.TestCase):
    def _row(self, label: str) -> SimpleNamespace:
        return SimpleNamespace(route_label=label)

    def test_flags_empty_route_once_window_is_over(self) -> None:
        problems = missing_route_problems(
            [self._row(MORNING.label)], [MORNING, AFTERNOON],
            date(2026, 11, 27), datetime(2026, 11, 27, 19, 0), is_past=False,
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("Afternoon", problems[0])

    def test_does_not_flag_route_whose_window_is_still_open(self) -> None:
        problems = missing_route_problems(
            [self._row(MORNING.label)], [MORNING, AFTERNOON],
            date(2026, 11, 27), datetime(2026, 11, 27, 16, 0), is_past=False,
        )
        self.assertEqual(problems, [])

    def test_flags_any_empty_route_on_past_day(self) -> None:
        problems = missing_route_problems(
            [], [MORNING, AFTERNOON], date(2026, 11, 26), datetime(2026, 11, 27, 9, 0), is_past=True,
        )
        self.assertEqual(len(problems), 2)


if __name__ == "__main__":
    unittest.main()
