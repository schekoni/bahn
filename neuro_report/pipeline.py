from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .config import PipelineConfig, default_date_range
from .filters import passes_clinical_scope
from .models import Study
from .pubmed_client import PubMedClient
from .report_builder import build_pdf_report
from .scoring import score_study


def run_pipeline(config: PipelineConfig, start_date: date | None = None, end_date: date | None = None) -> tuple[Path, Path, list[Study]]:
    range_start, range_end = (start_date, end_date) if start_date and end_date else default_date_range()

    client = PubMedClient(email=config.email_for_pubmed)
    pmids = client.search_pmids(range_start, range_end, max_results=config.max_candidates)
    fetched = client.fetch_studies(pmids)

    selected: list[Study] = []
    for study in fetched:
        include, notes = passes_clinical_scope(study)
        if not include:
            continue
        study.clinical_relevance_notes.extend(notes)
        selected.append(score_study(study))

    top_studies = sorted(selected, key=lambda s: s.score, reverse=True)[: config.top_n]

    stamp = date.today().isoformat()
    report_dir = config.output_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / f"neuro_report_{range_start.isoformat()}_{range_end.isoformat()}_{stamp}.json"
    pdf_path = report_dir / f"neuro_report_{range_start.isoformat()}_{range_end.isoformat()}_{stamp}.pdf"

    _write_json_export(top_studies, json_path)
    build_pdf_report(top_studies, pdf_path, range_start, range_end, generated_on=date.today())

    return pdf_path, json_path, top_studies


def run_demo_pipeline(config: PipelineConfig, start_date: date | None = None, end_date: date | None = None) -> tuple[Path, Path, list[Study]]:
    range_start, range_end = (start_date, end_date) if start_date and end_date else default_date_range()
    demo_studies = _demo_studies()
    top_studies = sorted([score_study(s) for s in demo_studies], key=lambda s: s.score, reverse=True)[: config.top_n]

    stamp = date.today().isoformat()
    report_dir = config.output_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / f"neuro_report_demo_{range_start.isoformat()}_{range_end.isoformat()}_{stamp}.json"
    pdf_path = report_dir / f"neuro_report_demo_{range_start.isoformat()}_{range_end.isoformat()}_{stamp}.pdf"
    _write_json_export(top_studies, json_path)
    build_pdf_report(top_studies, pdf_path, range_start, range_end, generated_on=date.today())
    return pdf_path, json_path, top_studies


def _write_json_export(studies: list[Study], path: Path) -> None:
    payload = []
    for study in studies:
        row = asdict(study)
        if study.publication_date:
            row["publication_date"] = study.publication_date.isoformat()
        payload.append(row)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monatlicher Neurologie-Report (Stroke/Notfall)")
    parser.add_argument("--email", help="Kontakt-E-Mail für PubMed API")
    parser.add_argument("--start-date", help="Startdatum YYYY-MM-DD")
    parser.add_argument("--end-date", help="Enddatum YYYY-MM-DD")
    parser.add_argument("--max-candidates", type=int, default=300)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--output-dir", default="data/neuro_reports")
    parser.add_argument("--demo", action="store_true", help="Erzeugt Offline-Beispielreport ohne API-Aufruf")
    return parser.parse_args()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def main() -> None:
    args = parse_args()
    if not args.demo and not args.email:
        raise SystemExit("--email ist erforderlich, wenn --demo nicht gesetzt ist.")

    config = PipelineConfig(
        email_for_pubmed=args.email or "demo@example.com",
        max_candidates=args.max_candidates,
        top_n=args.top_n,
        output_dir=Path(args.output_dir),
    )

    if args.demo:
        pdf_path, json_path, top_studies = run_demo_pipeline(
            config=config,
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
        )
    else:
        pdf_path, json_path, top_studies = run_pipeline(
            config=config,
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
        )

    print(f"PDF erstellt: {pdf_path}")
    print(f"JSON exportiert: {json_path}")
    print(f"Anzahl priorisierter Studien: {len(top_studies)}")


def _demo_studies() -> list[Study]:
    return [
        Study(
            pmid="DEMO0001",
            title="Multicenter randomized trial of direct thrombectomy workflow in acute ischemic stroke",
            journal="Stroke",
            publication_date=date(2026, 1, 12),
            abstract=(
                "Randomized controlled trial in 1520 patients with acute ischemic stroke. "
                "Primary endpoint: functional outcome at 90 days. HR 0.82 (95% CI 0.72-0.93), p=0.002. "
                "OR 1.36 (95% CI 1.18-1.57), mortality reduced."
            ),
            publication_types=["Randomized Controlled Trial", "Multicenter Study"],
            affiliations=["University Hospital Berlin, Germany", "Mayo Clinic, United States"],
            country_hints=["Deutschland", "USA"],
            doi="10.0000/demo.1",
        ),
        Study(
            pmid="DEMO0002",
            title="International registry analysis of door-to-needle optimization in emergency departments",
            journal="Neurology",
            publication_date=date(2026, 1, 21),
            abstract=(
                "Prospective registry including n=2100 in emergency department stroke pathways. "
                "Reduced treatment delay with improved disability outcomes. RR 0.88 (95% CI 0.80-0.96), p=0.004."
            ),
            publication_types=["Observational Study", "Multicenter Study"],
            affiliations=["Toronto Stroke Program, Canada", "University of Melbourne, Australia"],
            country_hints=["Kanada", "Australien"],
            doi="10.0000/demo.2",
        ),
        Study(
            pmid="DEMO0003",
            title="Systematic review and meta-analysis on blood pressure targets in intracerebral hemorrhage",
            journal="Lancet Neurology",
            publication_date=date(2026, 1, 25),
            abstract=(
                "Meta-analysis of 17 trials on intracerebral hemorrhage acute care. "
                "Functional outcome and mortality analyzed. OR 1.14 (95% CI 1.03-1.27), p=0.01."
            ),
            publication_types=["Meta-Analysis", "Systematic Review"],
            affiliations=["Paris Brain Institute, France", "Karolinska Institute, Sweden"],
            country_hints=["Frankreich", "Schweden"],
            doi="10.0000/demo.3",
        ),
    ]


if __name__ == "__main__":
    main()
