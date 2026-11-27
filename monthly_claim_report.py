from __future__ import annotations

import argparse
import os
import smtplib
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo


TZ_NAME = "Europe/Berlin"
TRAIN_FILTERS = (
    ("Morning Freiburg->Offenburg", "ICE376"),
    ("Morning Freiburg->Offenburg", "ECE8"),
    ("Afternoon Offenburg->Freiburg", "ICE73"),
    ("Afternoon Offenburg->Freiburg", "ICE373"),
    ("Afternoon Offenburg->Freiburg", "RE5339"),
)

# Flat compensation per case from 60 minutes delay at the destination for season
# tickets (bahn.de, Fahrgastrechte "Regelungen"). Season-ticket holders except
# BahnCard 100 may also collect cases from 20 minutes; how collected minutes turn
# into money is not spelled out by DB, so the estimate counts one flat amount per
# 60 collected minutes and is labelled as an estimate.
TARIFFS = {
    "fv2": ("Fernverkehr-Zeitkarte, 2. Klasse", 5.00, True),
    "fv1": ("Fernverkehr-Zeitkarte, 1. Klasse", 7.50, True),
    "nv2": ("Nahverkehr-Zeitkarte, 2. Klasse", 1.50, True),
    "nv1": ("Nahverkehr-Zeitkarte, 1. Klasse", 2.25, True),
    "bc100_2": ("BahnCard 100, 2. Klasse", 10.00, False),
    "bc100_1": ("BahnCard 100, 1. Klasse", 15.00, False),
}
DEFAULT_TARIFF = "fv2"
MIN_PAYOUT_EUR = 4.00


@dataclass(frozen=True)
class Case:
    service_date: str
    train_name: str
    route_label: str
    planned_departure: str
    planned_arrival: str | None
    actual_arrival: str | None
    delay_minutes: int | None
    canceled: bool


def previous_month(today: date) -> tuple[date, date]:
    month_start = today.replace(day=1)
    end = month_start - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def keep_one_case_per_commute(cases: list[Case]) -> list[Case]:
    """Only one train is ridden per route and day: keep the most delayed one.

    Several claim trains run in each commute window (e.g. ICE376 and ECE8 in the
    morning). Claiming more than one of them for the same trip would be wrong.
    Cancellations stay listed separately because they must be checked by hand.
    """
    selected: list[Case] = []
    best: dict[tuple[str, str], Case] = {}
    for case in cases:
        if case.canceled or case.delay_minutes is None:
            selected.append(case)
            continue
        key = (case.service_date, case.route_label)
        current = best.get(key)
        if current is None or (
            case.delay_minutes > current.delay_minutes
            or (
                case.delay_minutes == current.delay_minutes
                and case.planned_departure < current.planned_departure
            )
        ):
            best[key] = case
    selected.extend(best.values())
    return sorted(selected, key=lambda item: (item.service_date, item.planned_departure, item.train_name))


# Backwards-compatible name.
keep_most_delayed_afternoon = keep_one_case_per_commute


@dataclass(frozen=True)
class Compensation:
    tariff_label: str
    rate: float
    full_cases: int
    collected_minutes: int
    collected_units: int
    canceled_open: int
    total: float
    cap: float | None

    @property
    def payable(self) -> float:
        return min(self.total, self.cap) if self.cap is not None else self.total


def estimate_compensation(
    cases: list[Case], tariff_key: str | None = None, ticket_price: float | None = None
) -> Compensation:
    tariff_key = (tariff_key or os.getenv("CLAIM_TARIFF") or DEFAULT_TARIFF).strip().lower()
    label, rate, can_collect = TARIFFS.get(tariff_key, TARIFFS[DEFAULT_TARIFF])
    if ticket_price is None:
        raw_price = (os.getenv("CLAIM_TICKET_PRICE") or "").replace(",", ".").strip()
        ticket_price = float(raw_price) if raw_price else None

    full = [c for c in cases if not c.canceled and c.delay_minutes is not None and c.delay_minutes >= 60]
    collected = [
        c for c in cases
        if not c.canceled and c.delay_minutes is not None and 20 <= c.delay_minutes < 60
    ]
    collected_minutes = sum(c.delay_minutes for c in collected) if can_collect else 0
    units = collected_minutes // 60
    return Compensation(
        tariff_label=label,
        rate=rate,
        full_cases=len(full),
        collected_minutes=collected_minutes,
        collected_units=units,
        canceled_open=sum(1 for c in cases if c.canceled),
        total=rate * (len(full) + units),
        cap=round(ticket_price * 0.25, 2) if ticket_price else None,
    )


def format_eur(value: float) -> str:
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


SELECTIONS = {
    "most_delayed": "je Fahrt der am stärksten verspätete Zug",
    "least_delayed": "vorsichtig: je Fahrt der am wenigsten verspätete Zug",
}
DEFAULT_SELECTION = "most_delayed"


def _selection(selection: str | None) -> str:
    value = (selection or os.getenv("CLAIM_SELECTION") or DEFAULT_SELECTION).strip().lower()
    return value if value in SELECTIONS else DEFAULT_SELECTION


def keep_least_delayed_per_commute(candidates: list[Case]) -> list[Case]:
    """Conservative choice when it is unclear which train was taken.

    Per route and day the train with the smallest delay is assumed. If any
    claim train of that commute arrived less than 20 minutes late, the day is
    dropped. A day where every train was cancelled stays as a cancellation.
    """
    by_commute: dict[tuple[str, str], list[Case]] = {}
    for case in candidates:
        by_commute.setdefault((case.service_date, case.route_label), []).append(case)

    selected: list[Case] = []
    for group in by_commute.values():
        ran = [c for c in group if not c.canceled and c.delay_minutes is not None]
        if ran:
            least = min(ran, key=lambda c: (c.delay_minutes, c.planned_departure))
            if least.delay_minutes >= 20:
                selected.append(least)
        elif group:
            selected.append(min(group, key=lambda c: c.planned_departure))
    return sorted(selected, key=lambda item: (item.service_date, item.planned_departure, item.train_name))


def read_month(
    database: Path, start: date, end: date, selection: str | None = None
) -> tuple[list[Case], str | None, str | None]:
    if not database.exists():
        raise FileNotFoundError(f"Datenbank nicht gefunden: {database}")

    with sqlite3.connect(database) as con:
        bounds = con.execute(
            "SELECT MIN(service_date), MAX(service_date) FROM observations"
        ).fetchone()
        available_from, available_through = bounds
        # Every claim-train journey with a known outcome (on time, late or
        # cancelled); journeys without arrival information are unknown and skipped.
        candidates: list[Case] = []
        for route, train in TRAIN_FILTERS:
            rows = con.execute(
                """SELECT service_date, train_name, route_label, planned_departure,
                          planned_arrival, actual_arrival,
                          arrival_schedule_deviation_minutes, arrival_observed,
                          canceled, canceled_arrival
                   FROM observations
                   WHERE service_date BETWEEN ? AND ? AND route_label = ? AND train_name = ?
                   ORDER BY service_date""",
                (start.isoformat(), end.isoformat(), route, train),
            ).fetchall()
            for row in rows:
                (service_date, train_name, route_label, departure, planned_arrival,
                 actual_arrival, deviation, arrival_observed, canceled, canceled_arrival) = row
                flagged_canceled = bool(canceled or canceled_arrival)
                has_arrival = bool(arrival_observed) and actual_arrival is not None
                delay = max(0, int(deviation or 0)) if has_arrival else None
                # A partly cancelled train that still arrived with a real delay
                # counts with that delay. Otherwise the flag wins: the old
                # collector stored the *planned* arrival as actual for cancelled
                # trains (deviation 0, sometimes negative), which is not an arrival.
                if flagged_canceled and (delay is None or delay < 20):
                    is_canceled, delay = True, None
                else:
                    is_canceled = False
                if is_canceled or delay is not None:
                    candidates.append(Case(
                        service_date, train_name, route_label, departure,
                        planned_arrival, actual_arrival, delay, is_canceled,
                    ))

    if _selection(selection) == "least_delayed":
        return keep_least_delayed_per_commute(candidates), available_from, available_through
    claimable = [c for c in candidates if c.canceled or c.delay_minutes >= 20]
    return keep_one_case_per_commute(claimable), available_from, available_through


def _hhmm(raw: str | None) -> str:
    if not raw:
        return "unbekannt"
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return raw[-5:]


def build_message(month: str, cases: list[Case], available_from: str | None,
                  available_through: str | None, subscription_name: str,
                  subscription_number: str, traveler_name: str) -> tuple[str, str]:
    over_60 = [case for case in cases if case.delay_minutes is not None and case.delay_minutes >= 60]
    from_20 = [case for case in cases if case.delay_minutes is not None and 20 <= case.delay_minutes < 60]
    canceled = [case for case in cases if case.canceled]

    lines = [
        f"Monatsübersicht Fahrgastrechte – {month}",
        "",
        f"Erfasster Datenbestand: {available_from or 'keine Daten'} bis {available_through or 'keine Daten'}.",
    ]
    start = date.fromisoformat(f"{month}-01")
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    if not available_from or date.fromisoformat(available_from) > start or not available_through or date.fromisoformat(available_through) < month_end:
        lines.append("ACHTUNG: Die Datenbank deckt den gesamten Berichtsmonat nicht nachweislich ab. Fehlende Einträge sind kein Nachweis für pünktliche oder ausgefallene Fahrten.")
    estimate = estimate_compensation(cases)
    lines += [
        "",
        f"Auswahl: {SELECTIONS[_selection(None)]}.",
        f"Voraussichtliche Entschädigung (Schätzung, {estimate.tariff_label}): {format_eur(estimate.payable)}",
        f"  {estimate.full_cases} × ab 60 Min. à {format_eur(estimate.rate)}"
        f" + {estimate.collected_minutes} gesammelte Minuten aus Fällen ab 20 Min."
        f" (≈ {estimate.collected_units} × {format_eur(estimate.rate)})",
    ]
    if estimate.cap is not None:
        lines.append(f"  Obergrenze 25 % des Zeitkartenwerts: {format_eur(estimate.cap)}")
    if estimate.canceled_open:
        lines.append(f"  Nicht eingerechnet: {estimate.canceled_open} Ausfall/Ausfälle (einzeln prüfen).")
    lines += [
        "",
        "VORLAGE ZUR PRÜFUNG UND ANSCHLIESSENDEN EINREICHUNG BEI DER DB",
        "",
        "Sehr geehrte Damen und Herren,",
        "",
        f"hiermit bitte ich um Prüfung und Entschädigung im Rahmen meines Abos {subscription_name}. Bitte berücksichtigen Sie die nachfolgend aufgeführten, von mir tatsächlich genutzten Fahrten.",
        "",
        f"Abo-Nr.: {subscription_number}",
        f"Reisende Person: {traveler_name}",
        f"Berichtsmonat: {month}",
        "",
        "Verspätungen ab 60 Minuten am Zielbahnhof:",
    ]
    if over_60:
        lines.extend(f"- {c.service_date}: {c.train_name}, {_hhmm(c.planned_departure)} Uhr, {c.route_label.replace('Morning Freiburg->Offenburg', 'Freiburg–Offenburg').replace('Afternoon Offenburg->Freiburg', 'Offenburg–Freiburg')}; planmäßige Ankunft {_hhmm(c.planned_arrival)}, tatsächliche Ankunft {_hhmm(c.actual_arrival)} (+{c.delay_minutes} Min.)" for c in over_60)
    else:
        lines.append("- Keine erfassten Fälle.")
    lines += ["", "Weitere Verspätungsfälle ab 20 und unter 60 Minuten (zur gebündelten Prüfung):"]
    if from_20:
        lines.extend(f"- {c.service_date}: {c.train_name}, {_hhmm(c.planned_departure)} Uhr, {c.route_label.replace('Morning Freiburg->Offenburg', 'Freiburg–Offenburg').replace('Afternoon Offenburg->Freiburg', 'Offenburg–Freiburg')}; Ankunft +{c.delay_minutes} Min. (planmäßig {_hhmm(c.planned_arrival)}, tatsächlich {_hhmm(c.actual_arrival)})" for c in from_20)
    else:
        lines.append("- Keine erfassten Fälle.")
    lines += ["", "Ausfälle ohne erfasste tatsächliche Ankunft (bitte einzeln prüfen):"]
    if canceled:
        lines.extend(f"- {c.service_date}: {c.train_name}, {_hhmm(c.planned_departure)} Uhr, {c.route_label.replace('Morning Freiburg->Offenburg', 'Freiburg–Offenburg').replace('Afternoon Offenburg->Freiburg', 'Offenburg–Freiburg')} (Ausfall in den Daten markiert; tatsächliche Reise/Ankunft bitte prüfen)" for c in canceled)
    else:
        lines.append("- Keine erfassten Ausfälle.")
    lines += [
        "",
        "Hinweis: Bitte vor dem Versand alle Fahrten auf tatsächliche Nutzung und Abo-Gültigkeit prüfen. Nach den DB-Angaben werden Zeitkartenfahrten ab 60 Minuten am Zielbahnhof pauschal berücksichtigt; Fälle ab 20 Minuten können von Zeitkarteninhabern (außer BahnCard 100) innerhalb des Gültigkeitszeitraums gesammelt eingereicht werden. Diese Übersicht ist keine automatische Anspruchsbestätigung.",
        "",
        "Mit freundlichen Grüßen",
        traveler_name,
    ]
    subject = f"Entwurf Fahrgastrechte Monatsabo – {month}"
    return subject, "\n".join(lines)


def send_mail(subject: str, body: str) -> None:
    required = ("REPORT_EMAIL_TO", "REPORT_EMAIL_FROM", "SMTP_USERNAME", "SMTP_PASSWORD")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError("Fehlende GitHub-Secrets/Umgebungswerte: " + ", ".join(missing))
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    message = EmailMessage()
    message["To"] = os.environ["REPORT_EMAIL_TO"]
    message["From"] = os.environ["REPORT_EMAIL_FROM"]
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Erstellt und versendet den monatlichen DB-Fahrgastrechte-Entwurf.")
    parser.add_argument("--month", help="Berichtsmonat YYYY-MM (standardmäßig der vorherige Monat)")
    parser.add_argument("--dry-run", action="store_true", help="Nur anzeigen, keine E-Mail versenden")
    parser.add_argument("--force", action="store_true", help="Zeit-/Datumssperre bei manuellem Test umgehen")
    args = parser.parse_args()

    now = datetime.now(ZoneInfo(TZ_NAME))
    if not args.force and not args.dry_run and (now.day != 1 or now.hour != 8):
        print(f"Übersprungen: regulärer Versand ist am Monatsersten um 08:00 {TZ_NAME} (lokal jetzt {now:%Y-%m-%d %H:%M}).")
        return 0
    if args.month:
        month_start = date.fromisoformat(args.month + "-01")
        if month_start.strftime("%Y-%m") != args.month:
            raise ValueError("Monat muss YYYY-MM sein")
        month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    else:
        month_start, month_end = previous_month(now.date())
    database = Path(os.getenv("DATABASE_PATH", "data/train_punctuality.db"))
    cases, available_from, available_through = read_month(database, month_start, month_end)
    claim_details = {
        "CLAIM_SUBSCRIPTION_NAME": os.getenv("CLAIM_SUBSCRIPTION_NAME", "").strip(),
        "CLAIM_SUBSCRIPTION_NUMBER": os.getenv("CLAIM_SUBSCRIPTION_NUMBER", "").strip(),
        "CLAIM_TRAVELER_NAME": os.getenv("CLAIM_TRAVELER_NAME", "").strip(),
    }
    missing_details = [name for name, value in claim_details.items() if not value]
    if missing_details:
        raise RuntimeError("Fehlende GitHub-Secrets/Umgebungswerte: " + ", ".join(missing_details))
    subject, body = build_message(
        month_start.strftime("%Y-%m"), cases, available_from, available_through,
        claim_details["CLAIM_SUBSCRIPTION_NAME"],
        claim_details["CLAIM_SUBSCRIPTION_NUMBER"],
        claim_details["CLAIM_TRAVELER_NAME"],
    )
    print(body)
    if args.dry_run:
        return 0
    send_mail(subject, body)
    print(f"Bericht für {month_start:%Y-%m} gesendet.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Fehler beim Monatsbericht: {exc}", file=sys.stderr)
        sys.exit(1)
