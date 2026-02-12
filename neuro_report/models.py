from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Study:
    pmid: str
    title: str
    journal: str
    publication_date: date | None
    abstract: str
    publication_types: list[str]
    affiliations: list[str]
    country_hints: list[str]
    doi: str | None = None
    score: int = 0
    score_breakdown: dict[str, int] = field(default_factory=dict)
    clinical_relevance_notes: list[str] = field(default_factory=list)
    key_statistics: list[str] = field(default_factory=list)
    context_statement: str = ""

    @property
    def countries_display(self) -> str:
        if not self.country_hints:
            return "Unklar"
        return ", ".join(sorted(set(self.country_hints))[:5])
