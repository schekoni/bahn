from __future__ import annotations

import argparse
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db_monitor.car_collector import collect_car_observations
from db_monitor.collector import collect_observations
from db_monitor.config import load_car_routes, load_route_windows, load_settings
from db_monitor.openboards_collector import collect_openboards_observations
from db_monitor.storage import ObservationStore


CATCH_UP_UNTIL_HOUR = 4
DEFAULT_BACKFILL_DAYS = 1


def collection_dates(today: date, early_hours_run: bool, backfill_days: int) -> list[date]:
    """Previous days are re-collected so a skipped or late run leaves no gap."""
    dates = [today - timedelta(days=offset) for offset in range(max(backfill_days, 0), 0, -1)]
    if early_hours_run and not dates:
        dates.append(today - timedelta(days=1))
    if not early_hours_run:
        dates.append(today)
    return dates


def missing_route_problems(
    rows: list,
    windows: list,
    service_date: date,
    now_local: datetime,
    is_past: bool,
) -> list[str]:
    """Flag a route that produced no trains although its window is over."""
    problems: list[str] = []
    found = {row.route_label for row in rows}
    for window in windows:
        window_over = is_past or now_local.replace(tzinfo=None) > datetime.combine(
            service_date, window.end_time
        ) + timedelta(hours=1)
        if window_over and window.label not in found:
            problems.append(f"Keine Züge für {window.label} am {service_date} gefunden.")
    return problems


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _create_backup(database_path: str, backup_dir: str, retention_days: int) -> Path | None:
    db_path = Path(database_path)
    if not db_path.exists():
        return None

    out_dir = Path(backup_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d")
    backup_path = out_dir / f"{db_path.stem}_{stamp}{db_path.suffix}"
    if not backup_path.exists():
        shutil.copy2(db_path, backup_path)

    cutoff = datetime.now() - timedelta(days=retention_days)
    prefix = f"{db_path.stem}_"
    suffix = db_path.suffix
    for item in out_dir.glob(f"{prefix}*{suffix}"):
        try:
            date_str = item.stem[len(prefix):]
            item_date = datetime.strptime(date_str, "%Y-%m-%d")
            if item_date < cutoff:
                item.unlink()
        except (ValueError, FileNotFoundError):
            continue

    return backup_path


def main(trains_only: bool = False) -> None:
    settings = load_settings()
    windows = load_route_windows()
    store = ObservationStore(settings.database_path)
    store.initialize()

    now_local = datetime.now(ZoneInfo(settings.timezone))
    today_local = now_local.date()
    # GitHub's scheduler often starts runs hours late, some after midnight. A run
    # in the early hours cannot see any of today's commuter trains yet.
    early_hours_run = now_local.hour < CATCH_UP_UNTIL_HOUR
    history_start = (today_local - timedelta(days=90)).isoformat()
    history_end = (today_local - timedelta(days=1)).isoformat()
    allowed_train_names = store.historical_train_names_by_route(history_start, history_end)
    fallback_dep_times = store.historical_departure_times_by_route_train(history_start, history_end)

    problems: list[str] = []
    if settings.db_api_provider == "openboards":
        backfill_days = int(os.getenv("BACKFILL_DAYS", str(DEFAULT_BACKFILL_DAYS)))
        dates = collection_dates(today_local, early_hours_run, backfill_days)
        for service_date in dates:
            is_past = service_date < today_local
            try:
                rows = collect_openboards_observations(
                    settings, windows, service_date=service_date
                )
            except Exception as exc:  # keep going: one failed day must not lose the others
                problems.append(f"Abruf für {service_date} fehlgeschlagen: {exc}")
                continue
            if is_past:
                # Backfill only adds finished rows; it never replaces stored data
                # with incomplete real-time information.
                rows = [row for row in rows if row.arrival_observed or row.canceled]
            rows = store.reuse_existing_train_ids(rows)
            stored = store.upsert_many(rows)
            print(f"{service_date}: stored {stored} train observations")
            problems.extend(
                missing_route_problems(rows, windows, service_date, now_local, is_past)
            )
    elif settings.db_api_provider == "timetables":
        rows = collect_observations(
            settings,
            windows,
            allowed_train_names_by_route=allowed_train_names,
            fallback_departure_times_by_route_train=fallback_dep_times,
        )
        print(f"Stored {store.upsert_many(rows)} train observations in {settings.database_path}")
    else:
        raise ValueError(
            "DB_API_PROVIDER must be either 'timetables' or 'openboards'."
        )

    if trains_only:
        print("Skipped car collection for train-only update")
    else:
        today_local_str = today_local.isoformat()
        existing_car_targets = store.existing_car_targets_for_date(today_local_str)
        car_rows = collect_car_observations(
            settings,
            load_car_routes(),
            existing_targets=existing_car_targets,
        )
        car_inserted = store.upsert_car_many(car_rows)
        print(f"Stored {car_inserted} car observations in {settings.database_path}")

    backup_enabled = _bool_env("BACKUP_ENABLED", True)
    backup_dir = os.getenv("BACKUP_DIR", "data/backups")
    retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "60"))
    if backup_enabled:
        backup_path = _create_backup(settings.database_path, backup_dir, retention_days)
        if backup_path is not None:
            print(f"Backup snapshot ready: {backup_path}")

    if problems:
        for problem in problems:
            # GitHub renders ::error:: lines as annotations; the failed run
            # triggers GitHub's own failure e-mail.
            print(f"::error::{problem}")
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect train and car punctuality observations.")
    parser.add_argument(
        "--trains-only",
        action="store_true",
        help="Update train observations without making car routing API requests.",
    )
    main(trains_only=parser.parse_args().trains_only)
