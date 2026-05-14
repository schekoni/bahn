from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from db_monitor.car_collector import collect_car_observations
from db_monitor.collector import collect_observations
from db_monitor.config import load_car_routes, load_route_windows, load_settings
from db_monitor.storage import ObservationStore


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


def main() -> None:
    settings = load_settings()
    windows = load_route_windows()
    store = ObservationStore(settings.database_path)
    store.initialize()

    today_local = datetime.now(ZoneInfo(settings.timezone)).date()
    history_start = (today_local - timedelta(days=90)).isoformat()
    history_end = (today_local - timedelta(days=1)).isoformat()
    allowed_train_names = store.historical_train_names_by_route(history_start, history_end)
    fallback_dep_times = store.historical_departure_times_by_route_train(history_start, history_end)

    rows = collect_observations(
        settings,
        windows,
        allowed_train_names_by_route=allowed_train_names,
        fallback_departure_times_by_route_train=fallback_dep_times,
    )
    inserted = store.upsert_many(rows)
    print(f"Stored {inserted} train observations in {settings.database_path}")

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


if __name__ == "__main__":
    main()
