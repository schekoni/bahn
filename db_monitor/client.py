from __future__ import annotations

from datetime import date

import requests
from requests import Response

from db_monitor.config import Settings


class DBApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "DB-Client-Id": settings.client_id,
                "DB-Api-Key": settings.api_key,
            }
        )

    @staticmethod
    def _raise_with_context(response: Response, endpoint: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            body = (response.text or "").strip().replace("\n", " ")
            body = body[:500]
            raise requests.HTTPError(
                f"{exc} | endpoint={endpoint} | response_body={body}",
                response=response,
            ) from exc

    def get_station_eva(self, station_name: str) -> str:
        response = self.session.get(
            f"{self.settings.station_endpoint}/stations",
            params={"searchstring": station_name, "limit": 5},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        self._raise_with_context(response, "station-data/v2/stations")
        payload = response.json()
        result_set = payload.get("result", [])
        if not result_set:
            raise ValueError(f"No station found for '{station_name}'.")

        for station in result_set:
            candidate_name = (station.get("name") or "").lower()
            if station_name.lower() in candidate_name:
                return str(station["evaNumbers"][0]["number"])

        return str(result_set[0]["evaNumbers"][0]["number"])

    def get_plan(self, eva: str, service_date: date, hour: int) -> str:
        date_token = service_date.strftime("%y%m%d")
        response = self.session.get(
            f"{self.settings.timetables_endpoint}/plan/{eva}/{date_token}/{hour:02d}",
            headers={"Accept": "application/xml"},
            timeout=30,
        )
        if response.status_code == 404:
            # Some stations/hours legitimately have no plan payload.
            # Return an empty timetable so collectors can continue.
            return "<timetable/>"
        self._raise_with_context(response, "timetables/v1/plan")
        return response.text

    def get_changes(self, eva: str) -> str:
        response = self.session.get(
            f"{self.settings.timetables_endpoint}/fchg/{eva}",
            headers={"Accept": "application/xml"},
            timeout=30,
        )
        self._raise_with_context(response, "timetables/v1/fchg")
        return response.text
