from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .models import Study
from .summarizer import summarize_study

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT_MARGIN = 44
RIGHT_MARGIN = 44
TOP_MARGIN = 800
BOTTOM_MARGIN = 48


@dataclass
class RenderLine:
    text: str
    style: str
    indent: int = 0


STYLE_MAP = {
    "title": {"font": "F2", "size": 20, "leading": 24},
    "subtitle": {"font": "F1", "size": 11, "leading": 16},
    "section": {"font": "F2", "size": 14, "leading": 18},
    "study_header": {"font": "F2", "size": 15, "leading": 20},
    "meta": {"font": "F1", "size": 9, "leading": 12},
    "label": {"font": "F2", "size": 10, "leading": 14},
    "body": {"font": "F1", "size": 10, "leading": 14},
    "bullet": {"font": "F1", "size": 10, "leading": 14},
    "callout": {"font": "F3", "size": 10, "leading": 14},
    "spacer": {"font": "F1", "size": 10, "leading": 10},
    "divider": {"font": "F1", "size": 10, "leading": 10},
}


def build_pdf_report(
    studies: list[Study],
    output_path: Path,
    start_date: date,
    end_date: date,
    generated_on: date,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = _build_newsletter_lines(studies, start_date, end_date, generated_on)
    pages = _paginate_lines(lines)
    pdf_bytes = _render_pdf(pages)
    output_path.write_bytes(pdf_bytes)
    return output_path


def _build_newsletter_lines(studies: list[Study], start_date: date, end_date: date, generated_on: date) -> list[RenderLine]:
    lines: list[RenderLine] = []
    _add_wrapped(lines, "Neurologie Clinical Update", "title")
    _add_wrapped(lines, "Monatsnewsletter: Schlaganfall und Neuro-Notfallmedizin", "section")
    _add_wrapped(
        lines,
        f"Berichtszeitraum {start_date.isoformat()} bis {end_date.isoformat()} | Stand {generated_on.isoformat()}",
        "subtitle",
    )
    lines.append(RenderLine("", "spacer"))

    _add_wrapped(lines, "Editorial Snapshot", "section")
    _add_wrapped(
        lines,
        "Dieser Report priorisiert Studien mit potenziellem Einfluss auf Akutdiagnostik, Reperfusionsstrategien, Neuro-Intensivpfade und klinische Entscheidungsprozesse in der Notfallversorgung.",
        "body",
    )
    _add_wrapped(lines, f"Anzahl priorisierter Studien: {len(studies)}", "body")
    lines.append(RenderLine("", "spacer"))

    _add_wrapped(lines, "Top-Highlights", "section")
    if not studies:
        _add_wrapped(lines, "Keine geeigneten Studien im Zeitraum gefunden.", "body")
    else:
        for rank, study in enumerate(studies[:5], start=1):
            summary = summarize_study(study)
            _add_wrapped(
                lines,
                f"{rank}. {study.title}",
                "label",
            )
            _add_wrapped(
                lines,
                f"Score {study.score} | {study.journal or 'n/a'} | {summary['core_message']}",
                "bullet",
                indent=1,
            )
    lines.append(RenderLine("", "spacer"))

    _add_wrapped(lines, "Methodik und Auswahl", "section")
    _add_wrapped(lines, "Datenquelle: PubMed E-utilities, monatliches Publikationsfenster.", "body")
    _add_wrapped(
        lines,
        "Einschluss: klinische Studien in Stroke/Neuro-Notfallmedizin (RCT, Metaanalyse, hochwertige Kohorten/Register).",
        "body",
    )
    _add_wrapped(lines, "Ausschluss: Labor-/Tiermodelle, Fallberichte, seltene Nischenindikationen ohne Breitenrelevanz.", "body")
    _add_wrapped(lines, "Bewertung: Design, Stichprobengroesse, harte Endpunkte, Statistikrobustheit, Generalisierbarkeit, Leitlinienpotenzial.", "body")
    lines.append(RenderLine("", "spacer"))
    lines.append(RenderLine("", "divider"))

    for idx, study in enumerate(studies, start=1):
        summary = summarize_study(study)
        _add_wrapped(lines, f"Studie {idx}: {study.title}", "study_header")

        meta = (
            f"Journal: {study.journal or 'n/a'} | Datum: {study.publication_date.isoformat() if study.publication_date else 'n/a'} | "
            f"Laender: {study.countries_display} | PMID: {study.pmid}"
        )
        if study.doi:
            meta = f"{meta} | DOI: {study.doi}"
        _add_wrapped(lines, meta, "meta")

        _section_block(lines, "Kernbotschaft", str(summary["core_message"]))
        _section_block(lines, "Studiendesign", str(summary["methods"]))
        _section_block(lines, "Population", str(summary["population"]))
        _section_block(lines, "Primaerer Endpunkt", str(summary["endpoint"]))
        _section_block(lines, "Hauptergebnis", str(summary["result"]))

        _add_wrapped(lines, "Statistische Eckpunkte", "label")
        stats = summary["stats"]
        if isinstance(stats, list):
            for stat in stats:
                _add_wrapped(lines, f"- {stat}", "bullet", indent=1)
        else:
            _add_wrapped(lines, f"- {stats}", "bullet", indent=1)

        _section_block(lines, "Klinische Einordnung", study.context_statement or "Keine Einordnung vorhanden.")
        _section_block(lines, "Was koennte sich in der Praxis aendern", str(summary["clinical_shift"]))
        _section_block(lines, "Limitationen", str(summary["limitations"]))

        score_line = (
            "Score Breakdown: "
            f"Design {study.score_breakdown.get('design', 0)}, "
            f"N {study.score_breakdown.get('sample_size', 0)}, "
            f"Endpunkte {study.score_breakdown.get('hard_endpoints', 0)}, "
            f"Statistik {study.score_breakdown.get('effect_stats', 0)}, "
            f"Generalisierbarkeit {study.score_breakdown.get('generalizability', 0)}, "
            f"Leitlinienpotenzial {study.score_breakdown.get('guideline_impact', 0)} | Gesamt {study.score}"
        )
        _add_wrapped(lines, score_line, "callout")

        lines.append(RenderLine("", "spacer"))
        lines.append(RenderLine("", "divider"))

    _add_wrapped(lines, "Abschluss und Hinweis", "section")
    _add_wrapped(
        lines,
        "Automatische Extraktion kann statistische Details oder Subgruppenbefunde unvollstaendig erfassen. Vor SOP- oder Leitlinienanpassung ist ein manueller Volltext-Review erforderlich.",
        "body",
    )
    return lines


def _section_block(lines: list[RenderLine], label: str, text: str) -> None:
    _add_wrapped(lines, label, "label")
    _add_wrapped(lines, text, "body", indent=1)


def _add_wrapped(lines: list[RenderLine], text: str, style: str, indent: int = 0) -> None:
    if style == "divider":
        lines.append(RenderLine("", "divider"))
        return
    if not text:
        lines.append(RenderLine("", "spacer"))
        return

    width_chars = _max_chars_for_style(style, indent)
    wrapped = _wrap(text, width_chars)
    for chunk in wrapped:
        lines.append(RenderLine(chunk, style, indent=indent))


def _max_chars_for_style(style: str, indent: int) -> int:
    cfg = STYLE_MAP.get(style, STYLE_MAP["body"])
    text_width = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN - indent * 18
    approx_char_width = max(4.6, cfg["size"] * 0.53)
    return max(28, int(text_width / approx_char_width))


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    result: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            result.append(current)
            current = word
    result.append(current)
    return result


def _line_height(line: RenderLine) -> int:
    return int(STYLE_MAP.get(line.style, STYLE_MAP["body"])["leading"])


def _paginate_lines(lines: list[RenderLine]) -> list[list[RenderLine]]:
    pages: list[list[RenderLine]] = []
    current: list[RenderLine] = []
    current_height = 0
    max_height = TOP_MARGIN - BOTTOM_MARGIN

    for line in lines:
        needed = _line_height(line)
        if current and current_height + needed > max_height:
            pages.append(current)
            current = []
            current_height = 0
        current.append(line)
        current_height += needed

    if current:
        pages.append(current)
    return pages or [[RenderLine("Keine Inhalte", "body")]]


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_page_stream(page_lines: list[RenderLine], page_number: int, total_pages: int) -> bytes:
    y = TOP_MARGIN
    cmds: list[str] = []

    for line in page_lines:
        cfg = STYLE_MAP.get(line.style, STYLE_MAP["body"])

        if line.style == "divider":
            y -= 5
            cmds.append(f"0.7 w {LEFT_MARGIN} {y} m {PAGE_WIDTH - RIGHT_MARGIN} {y} l S")
            y -= cfg["leading"] - 5
            continue

        if line.style == "spacer":
            y -= cfg["leading"]
            continue

        x = LEFT_MARGIN + line.indent * 18
        safe = _escape_pdf_text(line.text)
        cmds.append("BT")
        cmds.append(f"/{cfg['font']} {cfg['size']} Tf")
        cmds.append(f"{x} {y} Td")
        cmds.append(f"({safe}) Tj")
        cmds.append("ET")
        y -= cfg["leading"]

    footer = _escape_pdf_text(f"Seite {page_number}/{total_pages}")
    cmds.extend(["BT", "/F1 9 Tf", f"{PAGE_WIDTH - RIGHT_MARGIN - 60} {BOTTOM_MARGIN - 10} Td", f"({footer}) Tj", "ET"])

    return "\n".join(cmds).encode("latin-1", errors="replace")


def _render_pdf(pages: list[list[RenderLine]]) -> bytes:
    objects: list[bytes] = []

    # 1 catalog, 2 pages root, 3 regular font, 4 bold font, 5 oblique font
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"placeholder-pages")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique >>")

    page_object_ids: list[int] = []

    for idx, page_lines in enumerate(pages, start=1):
        stream = _render_page_stream(page_lines, idx, len(pages))
        content_obj = f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream"
        objects.append(content_obj)
        content_id = len(objects)

        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        objects.append(page_obj)
        page_object_ids.append(len(objects))

    kids = " ".join([f"{pid} 0 R" for pid in page_object_ids])
    objects[1] = f"<< /Type /Pages /Count {len(page_object_ids)} /Kids [{kids}] >>".encode("ascii")

    pdf = bytearray()
    pdf.extend(b"%PDF-1.4\n")

    offsets = [0]
    for obj_id, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for idx in range(1, len(objects) + 1):
        pdf.extend(f"{offsets[idx]:010d} 00000 n \n".encode("ascii"))

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF"
    )
    pdf.extend(trailer.encode("ascii"))
    return bytes(pdf)
