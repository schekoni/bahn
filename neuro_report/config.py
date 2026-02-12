from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass
class PipelineConfig:
    email_for_pubmed: str
    max_candidates: int = 300
    top_n: int = 10
    output_dir: Path = Path("data/neuro_reports")


def default_date_range(reference_day: date | None = None) -> tuple[date, date]:
    today = reference_day or date.today()
    first_of_current_month = date(today.year, today.month, 1)
    last_of_previous_month = first_of_current_month - timedelta(days=1)
    first_of_previous_month = date(last_of_previous_month.year, last_of_previous_month.month, 1)
    return first_of_previous_month, last_of_previous_month
