from __future__ import annotations

from db_monitor.car_collector import collect_car_observations
from db_monitor.collector import collect_observations
from db_monitor.config import load_car_routes, load_route_windows, load_settings
from db_monitor.storage import ObservationStore


def main() -> None:
    settings = load_settings()
    windows = load_route_windows()
    store = ObservationStore(settings.database_path)
    store.initialize()

    rows = collect_observations(settings, windows)
    inserted = store.upsert_many(rows)
    print(f"Stored {inserted} train observations in {settings.database_path}")

    car_rows = collect_car_observations(settings, load_car_routes())
    car_inserted = store.upsert_car_many(car_rows)
    print(f"Stored {car_inserted} car observations in {settings.database_path}")


if __name__ == "__main__":
    main()
