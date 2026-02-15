from __future__ import annotations

from datetime import date

import requests
from requests.adapters import HTTPAdapter
from requests import Response
from urllib3.util.retry import Retry

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
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

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
        endpoint = "timetables/v1/plan"
        url = f"{self.settings.timetables_endpoint}/plan/{eva}/{date_token}/{hour:02d}"
        try:
            response = self.session.get(
                url,
                headers={"Accept": "application/xml"},
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"WARN: {endpoint} request failed for {url}: {exc}")
            return "<timetable/>"
        if response.status_code == 404:
            # Some stations/hours legitimately have no plan payload.
            # Return an empty timetable so collectors can continue.
            return "<timetable/>"
        try:
            self._raise_with_context(response, endpoint)
        except requests.HTTPError:
            if 500 <= response.status_code < 600:
                print(f"WARN: {endpoint} temporary upstream error ({response.status_code}) for {url}")
                return "<timetable/>"
            raise
        return response.text

    def get_changes(self, eva: str) -> str:
        endpoint = "timetables/v1/fchg"
        url = f"{self.settings.timetables_endpoint}/fchg/{eva}"
        try:
            response = self.session.get(
                url,
                headers={"Accept": "application/xml"},
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"WARN: {endpoint} request failed for {url}: {exc}")
            return "<timetable/>"
        try:
            self._raise_with_context(response, endpoint)
        except requests.HTTPError:
            if 500 <= response.status_code < 600:
                print(f"WARN: {endpoint} temporary upstream error ({response.status_code}) for {url}")
                return "<timetable/>"
            raise
        return response.text
