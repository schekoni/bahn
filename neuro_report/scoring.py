from __future__ import annotations

import re

from .models import Study


def score_study(study: Study) -> Study:
    merged = f"{study.title} {study.abstract}".lower()

    breakdown = {
        "design": _score_design(study.publication_types, merged),
        "sample_size": _score_sample_size(merged),
        "hard_endpoints": _score_hard_endpoints(merged),
        "effect_stats": _score_effect_stats(study.abstract),
        "generalizability": _score_generalizability(merged),
        "guideline_impact": _score_guideline_impact(merged),
    }

    study.score_breakdown = breakdown
    study.score = sum(breakdown.values())
    study.key_statistics = extract_key_statistics(study.abstract)
    study.context_statement = build_context_statement(merged, study.score)
    return study


def _score_design(pub_types: list[str], merged: str) -> int:
    types = {p.lower() for p in pub_types}
    if "randomized controlled trial" in types:
        return 25
    if "meta-analysis" in types or "systematic review" in types:
        return 22
    if "clinical trial" in types:
        return 20
    if "observational study" in types or "cohort" in merged:
        return 15
    return 8


def _score_sample_size(merged: str) -> int:
    n = _extract_sample_size(merged)
    if n is None:
        return 6
    if n >= 2000:
        return 15
    if n >= 1000:
        return 13
    if n >= 500:
        return 10
    if n >= 200:
        return 8
    return 5


def _extract_sample_size(merged: str) -> int | None:
    patterns = [
        r"\bn\s*[=:]\s*(\d{2,6})\b",
        r"\bsample size\s*[=:]?\s*(\d{2,6})\b",
        r"\b(\d{2,6})\s+patients?\b",
        r"\b(\d{2,6})\s+participants?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, merged)
        if match:
            return int(match.group(1))
    return None


def _score_hard_endpoints(merged: str) -> int:
    hard_terms = ["mortality", "functional outcome", "mRS", "disability", "readmission", "hemorrhage"]
    found = sum(1 for t in hard_terms if t.lower() in merged)
    return min(20, found * 5)


def _score_effect_stats(abstract: str) -> int:
    stats = extract_key_statistics(abstract)
    if len(stats) >= 4:
        return 15
    if len(stats) >= 2:
        return 11
    if len(stats) == 1:
        return 7
    return 3


def _score_generalizability(merged: str) -> int:
    points = 0
    for term in ["multicenter", "multi-center", "registry", "international", "real-world"]:
        if term in merged:
            points += 4
    return min(15, points if points else 6)


def _score_guideline_impact(merged: str) -> int:
    if any(term in merged for term in ["guideline", "practice-changing", "standard of care"]):
        return 10
    if any(term in merged for term in ["thrombectomy", "thrombolysis", "door-to-needle", "triage"]):
        return 8
    return 5


def extract_key_statistics(text: str) -> list[str]:
    patterns = [
        r"\bHR\s*[=:]?\s*\d+(?:\.\d+)?(?:\s*\([^)]*\))?",
        r"\bOR\s*[=:]?\s*\d+(?:\.\d+)?(?:\s*\([^)]*\))?",
        r"\bRR\s*[=:]?\s*\d+(?:\.\d+)?(?:\s*\([^)]*\))?",
        r"\bp\s*[<=>]\s*0?\.\d+",
        r"95%\s*CI\s*[=:]?\s*\(?\d+(?:\.\d+)?\s*[-,]\s*\d+(?:\.\d+)?\)?",
        r"\bNNT\s*[=:]?\s*\d+(?:\.\d+)?",
    ]
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            snippet = re.sub(r"\s+", " ", match).strip()
            if snippet not in found:
                found.append(snippet)
    return found[:8]


def build_context_statement(merged_text: str, score: int) -> str:
    if score >= 80:
        impact = "Sehr hohe potenzielle Praxisrelevanz"
    elif score >= 65:
        impact = "Hohe potenzielle Praxisrelevanz"
    else:
        impact = "Moderate potenzielle Praxisrelevanz"

    if "thrombectomy" in merged_text or "thrombolysis" in merged_text:
        context = "direkter Bezug zur akuten Reperfusionsstrategie"
    elif "intracerebral hemorrhage" in merged_text or "subarachnoid" in merged_text:
        context = "relevant für neurovaskuläre Notfallpfade"
    else:
        context = "relevant für neuro-notfallmedizinische Prozessoptimierung"

    return f"{impact}; {context}."
