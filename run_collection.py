from __future__ import annotations

from db_monitor.collector import collect_observations
from db_monitor.config import load_route_windows, load_settings
from db_monitor.storage import ObservationStore


def main() -> None:
    settings = load_settings()
    windows = load_route_windows()
    store = ObservationStore(settings.database_path)
    store.initialize()
    rows = collect_observations(settings, windows)
    inserted = store.upsert_many(rows)
    print(f"Stored {inserted} observations in {settings.database_path}")


if __name__ == "__main__":
    main()
