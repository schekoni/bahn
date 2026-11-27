from __future__ import annotations

import unittest

from monthly_claim_report import Case, estimate_compensation, format_eur, keep_one_case_per_commute

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
