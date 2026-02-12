from __future__ import annotations

from .models import Study

ALLOWED_PUBLICATION_TYPES = {
    "Randomized Controlled Trial",
    "Clinical Trial",
    "Meta-Analysis",
    "Systematic Review",
    "Observational Study",
    "Multicenter Study",
}

EXCLUDED_KEYWORDS = {
    "animal",
    "mouse",
    "mice",
    "rat",
    "zebrafish",
    "cell line",
    "in vitro",
    "rare mutation",
    "case report",
}

HIGH_RELEVANCE_TOPICS = {
    "ischemic stroke",
    "intracerebral hemorrhage",
    "subarachnoid hemorrhage",
    "thrombectomy",
    "thrombolysis",
    "emergency department",
    "neurocritical",
    "status epilepticus",
    "time to treatment",
}


def passes_clinical_scope(study: Study) -> tuple[bool, list[str]]:
    notes: list[str] = []
    merged = f"{study.title} {study.abstract}".lower()

    if any(term in merged for term in EXCLUDED_KEYWORDS):
        return False, ["Ausgeschlossen: präklinisch/seltene oder nicht-praktikable Evidenz."]

    if not any(topic in merged for topic in HIGH_RELEVANCE_TOPICS):
        return False, ["Ausgeschlossen: nicht klarer Stroke/Neuro-Notfall-Fokus."]

    pub_types = set(study.publication_types)
    if not pub_types.intersection(ALLOWED_PUBLICATION_TYPES):
        notes.append("Kein klarer High-Evidence-Publikationstyp erkannt.")

    if "multicenter" in merged or "multi-center" in merged:
        notes.append("Multicenter-Setting unterstützt Generalisierbarkeit.")
    if "guideline" in merged or "practice" in merged:
        notes.append("Direkter Bezug zur klinischen Versorgung.")

    return True, notes
