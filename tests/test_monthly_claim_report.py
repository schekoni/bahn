from __future__ import annotations

import unittest

from monthly_claim_report import (
    Case,
    estimate_compensation,
    format_eur,
    keep_least_delayed_per_commute,
    keep_one_case_per_commute,
)

MORNING = "Morning Freiburg->Offenburg"
EVENING = "Afternoon Offenburg->Freiburg"


def case(day: str, train: str, route: str, dep: str, delay: int | None, canceled: bool = False) -> Case:
    return Case(
        service_date=day, train_name=train, route_label=route,
        planned_departure=f"{day}T{dep}:00", planned_arrival=None, actual_arrival=None,
        delay_minutes=delay, canceled=canceled,
    )


class OneCasePerCommuteTests(unittest.TestCase):
    def test_keeps_most_delayed_train_per_route_and_day(self) -> None:
        kept = keep_one_case_per_commute([
            case("2026-11-27", "ICE376", MORNING, "06:55", 24),
            case("2026-11-27", "ECE8", MORNING, "07:27", 31),
            case("2026-11-27", "ICE373", EVENING, "16:30", 25),
            case("2026-11-27", "RE5339", EVENING, "16:35", 22),
        ])
        self.assertEqual([c.train_name for c in kept], ["ECE8", "ICE373"])

    def test_cancellations_stay_listed(self) -> None:
        kept = keep_one_case_per_commute([
            case("2026-11-25", "ECE8", MORNING, "07:27", None, canceled=True),
            case("2026-11-25", "ICE376", MORNING, "06:55", 21),
        ])
        self.assertEqual(sorted(c.train_name for c in kept), ["ECE8", "ICE376"])


class ReadMonthTests(unittest.TestCase):
    def test_partly_cancelled_train_with_arrival_counts_as_delay(self) -> None:
        import sqlite3
        from datetime import date
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from monthly_claim_report import read_month

        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            with sqlite3.connect(db) as con:
                con.execute("""CREATE TABLE observations (service_date TEXT, train_name TEXT,
                    route_label TEXT, planned_departure TEXT, planned_arrival TEXT, actual_arrival TEXT,
                    arrival_schedule_deviation_minutes INTEGER, arrival_observed INTEGER,
                    canceled INTEGER, canceled_arrival INTEGER)""")
                con.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)", [
                    # partly cancelled but arrived 106 min late
                    ("2026-09-28", "ICE373", EVENING, "2026-09-28T16:30:00", "2026-09-28T17:01:00",
                     "2026-09-28T18:47:00", 106, 1, 1, 0),
                    # really cancelled: no arrival
                    ("2026-09-03", "ECE8", MORNING, "2026-09-03T07:27:00", "2026-09-03T07:56:00",
                     None, 0, 1, 1, 1),
                    # really cancelled, old collector stored planned arrival as actual
                    ("2026-09-04", "ICE376", MORNING, "2026-09-04T06:55:00", "2026-09-04T07:26:00",
                     "2026-09-04T07:26:00", 0, 1, 1, 1),
                ])
            cases, _, _ = read_month(db, date(2026, 9, 1), date(2026, 9, 30))

        by_train = {c.train_name: c for c in cases}
        self.assertEqual((by_train["ICE373"].delay_minutes, by_train["ICE373"].canceled), (106, False))
        self.assertEqual((by_train["ECE8"].delay_minutes, by_train["ECE8"].canceled), (None, True))
        self.assertEqual((by_train["ICE376"].delay_minutes, by_train["ICE376"].canceled), (None, True))


class LeastDelayedTests(unittest.TestCase):
    def test_day_dropped_when_another_train_was_nearly_on_time(self) -> None:
        kept = keep_least_delayed_per_commute([
            case("2026-09-01", "ICE376", MORNING, "06:55", 24),
            case("2026-09-01", "ECE8", MORNING, "07:27", 9),
        ])
        self.assertEqual(kept, [])

    def test_takes_smallest_delay_when_all_trains_were_late(self) -> None:
        kept = keep_least_delayed_per_commute([
            case("2026-09-04", "ICE73", EVENING, "16:00", 64),
            case("2026-09-04", "ICE373", EVENING, "16:30", 83),
            case("2026-09-04", "RE5339", EVENING, "16:35", 31),
        ])
        self.assertEqual([(c.train_name, c.delay_minutes) for c in kept], [("RE5339", 31)])

    def test_cancellation_only_if_nothing_ran(self) -> None:
        all_cancelled = keep_least_delayed_per_commute([
            case("2026-09-03", "ICE376", MORNING, "06:55", None, canceled=True),
            case("2026-09-03", "ECE8", MORNING, "07:27", None, canceled=True),
        ])
        self.assertEqual([c.train_name for c in all_cancelled], ["ICE376"])
        one_ran_on_time = keep_least_delayed_per_commute([
            case("2026-09-05", "ICE376", MORNING, "06:55", None, canceled=True),
            case("2026-09-05", "ECE8", MORNING, "07:27", 2),
        ])
        self.assertEqual(one_ran_on_time, [])


class CompensationTests(unittest.TestCase):
    CASES = [
        case("2026-11-18", "ICE373", EVENING, "16:30", 199),
        case("2026-11-19", "ICE73", EVENING, "16:00", 67),
        case("2026-11-20", "ICE373", EVENING, "16:30", 40),
        case("2026-11-21", "ICE73", EVENING, "16:00", 52),
        case("2026-11-24", "ICE373", EVENING, "16:30", 30),
        case("2026-11-25", "ECE8", MORNING, "07:27", None, canceled=True),
    ]

    def test_fernverkehr_second_class(self) -> None:
        result = estimate_compensation(self.CASES, "fv2", ticket_price=None)
        self.assertEqual((result.full_cases, result.collected_minutes, result.collected_units), (2, 122, 2))
        self.assertEqual(result.total, 20.0)  # (2 + 2) x 5 EUR
        self.assertEqual(result.canceled_open, 1)

    def test_bahncard_100_cannot_collect_short_delays(self) -> None:
        result = estimate_compensation(self.CASES, "bc100_2", ticket_price=None)
        self.assertEqual((result.collected_units, result.total), (0, 20.0))

    def test_cap_at_quarter_of_ticket_price(self) -> None:
        result = estimate_compensation(self.CASES, "fv2", ticket_price=60.0)
        self.assertEqual((result.cap, result.payable), (15.0, 15.0))

    def test_format_eur(self) -> None:
        self.assertEqual(format_eur(1234.5), "1.234,50 €")


if __name__ == "__main__":
    unittest.main()
