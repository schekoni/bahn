from __future__ import annotations

import re

from .models import Study

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def summarize_study(study: Study) -> dict[str, str | list[str]]:
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(study.abstract) if s.strip()]

    methods = _first_match(
        sentences,
        ["random", "trial", "cohort", "registry", "meta-analysis", "systematic", "prospective", "retrospective", "multicenter"],
    )
    population = _first_match(sentences, ["n=", "patients", "participants", "included", "enrolled"]) 
    endpoint = _first_match(sentences, ["primary endpoint", "primary outcome", "functional outcome", "mortality", "disability", "mrs"])
    result = _first_match(sentences, ["reduced", "improved", "increase", "decrease", "significant", "superior", "noninferior"]) 

    stats = study.key_statistics or ["Keine klar extrahierbaren Kennzahlen im Abstract."]

    core_message = _build_core_message(study, result, endpoint)
    clinical_shift = _practice_shift(study, result)
    limitations = _limitations(study, sentences)

    return {
        "core_message": core_message,
        "methods": methods or "Studienmethodik im Abstract nur teilweise beschrieben.",
        "population": population or "Population nicht eindeutig quantifizierbar aus dem Abstract.",
        "endpoint": endpoint or "Primaerer klinischer Endpunkt nicht klar benannt.",
        "result": result or "Hauptergebnis im Abstract nicht klar formuliert.",
        "stats": stats,
        "clinical_shift": clinical_shift,
        "limitations": limitations,
    }


def _first_match(sentences: list[str], needles: list[str]) -> str | None:
    for sentence in sentences:
        low = sentence.lower()
        if any(needle in low for needle in needles):
            return sentence
    return None


def _build_core_message(study: Study, result: str | None, endpoint: str | None) -> str:
    if result:
        return result
    if endpoint:
        return endpoint
    if study.context_statement:
        return study.context_statement
    return "Diese Studie adressiert eine potenziell praxisrelevante Frage in der akuten Schlaganfall- oder Neuro-Notfallversorgung."


def _practice_shift(study: Study, result: str | None) -> str:
    base = study.context_statement or "Potenzielle klinische Relevanz vorhanden"
    if study.score >= 75:
        urgency = "Hoch priorisieren fuer Team-Review und moegliche SOP-Anpassung."
    elif study.score >= 65:
        urgency = "Im Stroke/Notfall-Board diskutieren und auf lokale Uebertragbarkeit pruefen."
    else:
        urgency = "Als beobachtungsrelevante Evidenz einordnen; noch keine direkte SOP-Aenderung."

    if result and any(term in result.lower() for term in ["mortality", "functional outcome", "mrs", "disability"]):
        signal = "Signal auf patientenrelevante Endpunkte vorhanden."
    else:
        signal = "Signal eher prozess- oder surrogatbezogen."

    return f"{base} {signal} {urgency}"


def _limitations(study: Study, sentences: list[str]) -> str:
    low_abstract = study.abstract.lower()
    limitation_sentence = _first_match(sentences, ["limitation", "limitations", "caution", "interpreted with caution"])
    if limitation_sentence:
        return limitation_sentence

    if "single-center" in low_abstract or "single center" in low_abstract:
        return "Moegliche Limitierung durch Single-Center-Setting und eingeschraenkte Generalisierbarkeit."
    if not study.key_statistics:
        return "Wichtige statistische Detailwerte sind im Abstract unvollstaendig; Volltextpruefung empfohlen."
    if study.score < 65:
        return "Evidenzsignal vorhanden, aber potenziell begrenzter Einfluss auf Leitlinien/Standardprozesse."
    return "Keine expliziten Limitationen im Abstract genannt; kritische Volltextbewertung bleibt erforderlich."
