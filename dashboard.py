from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import monthly_claim_report as claim_report
from db_monitor.config import load_car_routes, load_settings


ROUTE_ORDER = [
    "Morning Freiburg->Offenburg",
    "Afternoon Offenburg->Freiburg",
]

ROUTE_TITLES = {
    "Morning Freiburg->Offenburg": "Freiburg → Offenburg",
    "Afternoon Offenburg->Freiburg": "Offenburg → Freiburg",
}

CAR_ROUTE_BY_TRAIN_ROUTE = {
    "Morning Freiburg->Offenburg": "Car Morning Freiburg->Offenburg",
    "Afternoon Offenburg->Freiburg": "Car Afternoon Offenburg->Freiburg",
}


# Commute windows that matter for the dashboard. Collection runs wider
# (see MORNING_START/END in the workflow) so the windows can change later.
DISPLAY_WINDOWS = {
    "Morning Freiburg->Offenburg": ("06:30", "07:30"),
    "Afternoon Offenburg->Freiburg": ("15:30", "17:30"),
}

PRIORITY_TRAINS = {
    "Morning Freiburg->Offenburg": ["ECE8", "ICE376"],
    "Afternoon Offenburg->Freiburg": ["ICE73", "ICE373", "RE5339"],
}



def _clean_reason_text(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null", "nan", "-"}:
        return ""
    text = re.sub(r"\b(?:none|null|nan)\b", "", text, flags=re.IGNORECASE)
    text = text.replace("||", "|").strip(" |")
    parts = [p.strip() for p in text.split("|")]
    cleaned: list[str] = []
    for part in parts:
        # Messages from the old Timetables API carry type letters, message ids
        # (r123...), validity timestamps and code numbers next to the text.
        words = part.split()
        if any(_REASON_CODE.fullmatch(w) for w in words):
            words = [w for w in words if not _REASON_NOISE.fullmatch(w)]
        readable = " ".join(words)
        if readable and readable.lower() not in {"none", "null", "nan", "-"} and readable not in cleaned:
            cleaned.append(readable)
    return " | ".join(cleaned)


_REASON_CODE = re.compile(r"r\d{5,}|\d{10}")
_REASON_NOISE = re.compile(r"[a-z]|r\d+|\d+")


def _clean_label_text(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null", "nan", "-"}:
        return ""
    return text


def _coerce_datetime_naive(series: pd.Series, timezone: str = "Europe/Berlin") -> pd.Series:
    """Parse ISO timestamps into naive local time.

    Columns mix naive local values (planned times) with offset-aware ones whose
    offset changes with DST (+01:00 / +02:00). Parsing those together is
    deprecated in pandas, so aware and naive values are parsed separately.
    """
    text = series.astype("string").str.strip()
    aware = text.str.contains(r"(?:[+-]\d{2}:?\d{2}|Z)$", regex=True, na=False)
    result = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    if aware.any():
        result.loc[aware] = (
            pd.to_datetime(text[aware], utc=True, errors="coerce", format="ISO8601")
            .dt.tz_convert(timezone)
            .dt.tz_localize(None)
        )
    if (~aware).any():
        result.loc[~aware] = pd.to_datetime(text[~aware], errors="coerce", format="ISO8601")
    return result


@st.cache_data(ttl=600, show_spinner=False)
def _load_data_cached(db_path: str, timezone: str, db_mtime: float) -> pd.DataFrame:
    # db_mtime is part of the cache key: a new collector commit invalidates it.
    return load_data(db_path, timezone)


@st.cache_data(ttl=600, show_spinner=False)
def _load_car_data_cached(db_path: str, db_mtime: float) -> pd.DataFrame:
    return load_car_data(db_path)


def load_data(db_path: str, timezone: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(observations)").fetchall()}
        if not cols:
            return pd.DataFrame()

        train_name_expr = "train_name" if "train_name" in cols else "line"
        arrival_delay_expr = "arrival_delay_minutes" if "arrival_delay_minutes" in cols else "0"
        arrival_observed_expr = "arrival_observed" if "arrival_observed" in cols else "1"
        arrival_missing_expr = "arrival_info_missing" if "arrival_info_missing" in cols else "0"
        dep_reason_expr = "departure_reason" if "departure_reason" in cols else "''"
        arr_reason_expr = "arrival_reason" if "arrival_reason" in cols else "''"
        try:
            df = pd.read_sql_query(
                f"""
                SELECT
                    service_date,
                    train_id,
                    {train_name_expr} AS train_name,
                    line,
                    route_label,
                    observation_ts,
                    planned_departure,
                    planned_arrival,
                    actual_arrival,
                    delay_minutes,
                    {arrival_delay_expr} AS arrival_delay_minutes,
                    {arrival_observed_expr} AS arrival_observed,
                    {arrival_missing_expr} AS arrival_info_missing,
                    {dep_reason_expr} AS departure_reason,
                    {arr_reason_expr} AS arrival_reason,
                    canceled
                FROM observations
                ORDER BY service_date DESC, route_label, planned_departure
                """,
                con,
            )
        except Exception:
            return pd.DataFrame()

    if df.empty:
        return df

    for col in ("observation_ts", "planned_departure", "planned_arrival", "actual_arrival"):
        if col in df.columns:
            df[col] = _coerce_datetime_naive(df[col], timezone)

    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    df["canceled"] = df["canceled"].astype(bool)
    df["arrival_observed"] = df["arrival_observed"].astype(bool)
    df["arrival_info_missing"] = df["arrival_info_missing"].astype(bool)
    df["train_name"] = df["train_name"].map(_clean_label_text)
    df["line"] = df["line"].map(_clean_label_text)
    df["departure_reason"] = df["departure_reason"].map(_clean_reason_text)
    df["arrival_reason"] = df["arrival_reason"].map(_clean_reason_text)
    df["departure_hhmm"] = df["planned_departure"].dt.strftime("%H:%M")
    # One row per train: small timetable shifts (16:00 -> 16:04, construction
    # timetables) must not split a train into several rows. Label each train with
    # its most frequent planned departure time.
    df["train_key"] = [
        name or line or "Unbekannt" for name, line in zip(df["train_name"], df["line"])
    ]
    df["typical_hhmm"] = df.groupby(["route_label", "train_key"])["departure_hhmm"].transform(
        lambda s: s.value_counts().idxmax()
    )
    df["zug"] = df["train_key"] + " | " + df["typical_hhmm"]

    now_local = datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    deadline = df["planned_arrival"] + pd.to_timedelta(1, unit="h")
    today_local = now_local.date()
    inferred_missing = (~df["arrival_observed"]) & (df["planned_arrival"].notna()) & (deadline < now_local)
    inferred_missing_past = (~df["arrival_observed"]) & (df["service_date"] < today_local)
    df["effective_arrival_missing"] = df["arrival_info_missing"] | inferred_missing | inferred_missing_past
    df["effective_arrival_open"] = (~df["arrival_observed"]) & (~df["effective_arrival_missing"])

    df["effective_departure_unknown"] = (
        (~df["canceled"])
        & (df["observation_ts"] < df["planned_departure"])
        & (df["delay_minutes"] == 0)
    )

    suspicious_prearrival_zero = (
        df["arrival_observed"]
        & (df["planned_arrival"].notna())
        & (df["observation_ts"] < df["planned_arrival"])
        & (~df["canceled"])
    )
    df.loc[suspicious_prearrival_zero, "arrival_observed"] = False
    df.loc[suspicious_prearrival_zero, "effective_arrival_missing"] = False
    df.loc[suspicious_prearrival_zero, "effective_arrival_open"] = True

    return df


def load_car_data(db_path: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(car_observations)").fetchall()}
        if not cols:
            return pd.DataFrame()
        source_expr = "source" if "source" in cols else "'live'"
        df = pd.read_sql_query(
            f"""
            SELECT
                service_date,
                route_label,
                observation_ts,
                target_departure_time,
                duration_minutes,
                distance_km,
                {source_expr} AS source
            FROM car_observations
            ORDER BY service_date DESC, route_label
            """,
            con,
        )
    if df.empty:
        return df
    df["observation_ts"] = _coerce_datetime_naive(df["observation_ts"])
    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    return df


# ---------------------------------------------------------------------------
# Delay status scale, aligned with the passenger-rights thresholds used by the
# monthly claim mail: from 20 min a case can be claimed, from 60 min it counts
# in full. Colors: fixed status palette (validated in the dataviz reference),
# always shown together with a text label, never color alone.
# ---------------------------------------------------------------------------
CLAIM_MINUTES = 20
STATUS_ORDER = ["good", "warning", "serious", "critical"]
STATUS_COLORS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_INK = {"good": "#1a1a19", "warning": "#1a1a19", "serious": "#1a1a19", "critical": "#ffffff"}
STATUS_LABELS = {
    "good": "pünktlich (<5 min)",
    "warning": "5–19 min",
    "serious": "20–59 min · reklamierbar",
    "critical": "≥60 min oder Ausfall",
}
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MUTED = "color: rgba(128,128,128,0.9);"
WEEKEND_EMPTY = "background-color: rgba(128,128,128,0.10);"

PAGE_CSS = """
<style>
.block-container {padding-top: .6rem; padding-bottom: 1rem;}
header[data-testid="stHeader"] {height: 0; visibility: hidden;}
.bm-cards {display:flex; gap:12px; flex-wrap:wrap; margin:2px 0 10px;}
.bm-card {flex:1 1 170px; max-width:250px; border:1px solid rgba(128,128,128,.28);
          border-radius:10px; padding:9px 12px;}
.bm-head {display:flex; justify-content:space-between; gap:8px; font-weight:600;}
.bm-value {font-size:1.45rem; font-weight:700; line-height:1.25; margin:3px 0 1px;}
.bm-muted {opacity:.65; font-size:.82rem;}
.bm-status {font-size:.82rem; display:flex; align-items:center;}
.bm-dot {display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; flex:none;}
.bm-legend {display:flex; flex-direction:column; gap:4px; font-size:.85rem; margin:2px 0 6px;}
</style>
"""


def _delay_status(minutes: object, canceled: bool = False) -> str | None:
    if canceled:
        return "critical"
    if minutes is None or pd.isna(minutes):
        return None
    minutes = float(minutes)
    if minutes < 5:
        return "good"
    if minutes < CLAIM_MINUTES:
        return "warning"
    if minutes < 60:
        return "serious"
    return "critical"


def _status_chip(status: str, text: str | None = None) -> str:
    label = html.escape(text or STATUS_LABELS[status])
    return (
        f'<span class="bm-status"><span class="bm-dot" style="background:{STATUS_COLORS[status]}"></span>'
        f"{label}</span>"
    )


def _legend_html() -> str:
    return '<div class="bm-legend">' + "".join(_status_chip(s) for s in STATUS_ORDER) + "</div>"


def _build_car_commute_series(car_df: pd.DataFrame) -> pd.DataFrame:
    """One car value per day and train route; a live sample wins over a forecast."""
    columns = ["service_date", "route_label", "auto_minutes", "source"]
    if car_df.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for train_route, car_route in CAR_ROUTE_BY_TRAIN_ROUTE.items():
        tmp = car_df[car_df["route_label"] == car_route].copy()
        if tmp.empty:
            continue
        tmp["_live"] = tmp["source"].eq("live")
        for service_date, day in tmp.groupby("service_date"):
            chosen = day.sort_values(["_live", "observation_ts"], ascending=False, kind="stable").iloc[0]
            rows.append(
                {
                    "service_date": service_date,
                    "route_label": train_route,
                    "auto_minutes": int(chosen["duration_minutes"]),
                    "source": chosen["source"],
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _weekday_mean(series: pd.DataFrame) -> float | None:
    workdays = series[[d.weekday() < 5 for d in series["service_date"]]]
    if workdays.empty:
        return None
    return float(workdays["auto_minutes"].mean())


# ---------------------------------------------------------------------------
# 30-day matrix
# ---------------------------------------------------------------------------
def _cell_value(row: pd.Series, detailed: bool = False) -> str:
    if bool(row["canceled"]):
        return "Ausfall"
    if bool(row["arrival_observed"]):
        arr = int(float(row["arrival_delay_minutes"]))
        if not detailed:
            return f"+{arr}" if arr > 0 else "0"
        dep = "-" if bool(row["effective_departure_unknown"]) else str(int(float(row["delay_minutes"])))
        actual_arrival = row.get("actual_arrival")
        arrival_time = f" ({pd.to_datetime(actual_arrival):%H:%M})" if pd.notna(actual_arrival) else ""
        return f"S:{dep} A:{arr}{arrival_time}"
    # Not yet arrived (today) vs. no arrival information (past day).
    return "…" if bool(row.get("effective_arrival_open", False)) else "?"


def _style_day_cell(value: object) -> str:
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()
    if not text:
        return ""
    if text == "Ausfall":
        status = "critical"
    else:
        match = re.search(r"A:(\d+)", text) or re.fullmatch(r"\+?(\d+)", text)
        if not match:
            return MUTED
        status = _delay_status(int(match.group(1)))
    return (
        f"background-color: {STATUS_COLORS[status]}; color: {STATUS_INK[status]}; "
        "font-weight: 600; text-align: center;"
    )


def _style_weekend_day_cell(value: object) -> str:
    return _style_day_cell(value) or WEEKEND_EMPTY


def _style_avg_cell(value: object) -> str:
    text = str(value or "").strip()
    if not text.lstrip("+").isdigit():
        return MUTED
    status = _delay_status(int(text.lstrip("+")))
    return f"background-color: {STATUS_COLORS[status]}; color: {STATUS_INK[status]}; font-weight: 600;"


def _style_claim_cell(value: object) -> str:
    return "font-weight: 700;" if str(value or "0").strip() not in {"0", ""} else MUTED


STAT_COLS = ["Pünktl.", "Ø", "Reklam."]


def style_matrix(
    matrix: pd.DataFrame,
    day_cols: list[str],
    weekend_cols: list[str] | None = None,
) -> pd.io.formats.style.Styler:
    styler = matrix.style
    weekend = set(weekend_cols or [])
    weekday_cols = [c for c in day_cols if c not in weekend and c in matrix.columns]
    weekend_day_cols = [c for c in day_cols if c in weekend and c in matrix.columns]
    if weekday_cols:
        styler = styler.map(_style_day_cell, subset=weekday_cols)
    if weekend_day_cols:
        styler = styler.map(_style_weekend_day_cell, subset=weekend_day_cols)
    if "Ø" in matrix.columns:
        styler = styler.map(_style_avg_cell, subset=["Ø"])
    if "Reklam." in matrix.columns:
        styler = styler.map(_style_claim_cell, subset=["Reklam."])
    return styler


def build_route_matrix(
    df: pd.DataFrame, route_label: str, end_date: date, days: int = 30, detailed: bool = False
) -> tuple[pd.DataFrame, list[str], list[str]]:
    route_df = df[df["route_label"] == route_label]
    start_date = end_date - timedelta(days=days - 1)
    window = route_df[
        (route_df["service_date"] >= start_date) & (route_df["service_date"] <= end_date)
    ].copy()
    if window.empty:
        return pd.DataFrame(), [], []

    window["day_cell"] = window.apply(_cell_value, axis=1, detailed=detailed)
    pivot = window.pivot_table(index="zug", columns="service_date", values="day_cell", aggfunc="first")
    pivot = pivot.reindex(columns=sorted(pivot.columns, reverse=True))

    observed = window["arrival_observed"] & ~window["canceled"]
    arrivals = window[observed]
    # A cancellation counts as "not on time", consistent with the reliability chart.
    judged = window[observed | window["canceled"]]
    on_time = judged["arrival_observed"] & ~judged["canceled"] & (judged["arrival_delay_minutes"] < 5)
    on_time_pct = on_time.groupby(judged["zug"]).mean() * 100
    avg_delay = arrivals.groupby("zug")["arrival_delay_minutes"].mean()
    claimable = window["canceled"] | (observed & (window["arrival_delay_minutes"] >= CLAIM_MINUTES))
    claim_days = window[claimable].groupby("zug")["service_date"].nunique()

    stats = pd.DataFrame(index=pivot.index)
    stats["Pünktl."] = [
        f"{on_time_pct[z]:.0f} %" if z in on_time_pct.index else "–" for z in stats.index
    ]
    stats["Ø"] = [f"{avg_delay[z]:.0f}" if z in avg_delay.index else "–" for z in stats.index]
    stats["Reklam."] = [str(int(claim_days.get(z, 0))) for z in stats.index]

    day_cols: list[str] = []
    weekend_cols: list[str] = []
    rename: dict[object, str] = {}
    for day in pivot.columns:
        label = f"{WEEKDAYS[day.weekday()]} {day:%d.%m}"
        rename[day] = label
        day_cols.append(label)
        if day.weekday() >= 5:
            weekend_cols.append(label)

    order = window.groupby("zug")["typical_hhmm"].first().sort_values(kind="stable")
    matrix = stats.join(pivot.rename(columns=rename)).loc[order.index].fillna("")
    matrix.index.name = "Zug"
    return matrix.reset_index(), day_cols, weekend_cols


def _build_train_history(train_df: pd.DataFrame) -> pd.DataFrame:
    history_source = train_df.copy()
    history_source.loc[~history_source["arrival_observed"], ["arrival_delay_minutes"]] = pd.NA
    history = (
        history_source.groupby("service_date", as_index=False)
        .agg(
            start_delay=("delay_minutes", "mean"),
            arrival_delay=("arrival_delay_minutes", "mean"),
            canceled=("canceled", "max"),
            arrival_observed=("arrival_observed", "max"),
        )
        .sort_values("service_date")
    )
    history["start_delay"] = history["start_delay"].apply(lambda x: int(float(x)) if pd.notna(x) else 0)
    history["arrival_delay"] = history["arrival_delay"].apply(lambda x: int(float(x)) if pd.notna(x) else None)
    history["service_date"] = pd.to_datetime(history["service_date"])
    return history


def _reason_stats(train_df: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, object]] = []

    for _, row in train_df.iterrows():
        dep_reason = _clean_reason_text(row.get("departure_reason"))
        arr_reason = _clean_reason_text(row.get("arrival_reason"))
        dep_delay = int(float(row.get("delay_minutes", 0) or 0))
        arr_delay = int(float(row.get("arrival_delay_minutes", 0) or 0))
        canceled = bool(row.get("canceled", False))

        if canceled:
            records.append({"Bereich": "Start", "Grund": dep_reason or "Ausfall", "Verspätung": dep_delay})
            records.append({"Bereich": "Ankunft", "Grund": arr_reason or dep_reason or "Ausfall", "Verspätung": arr_delay})
            continue

        if dep_delay > 0:
            records.append({"Bereich": "Start", "Grund": dep_reason or "Unbekannt", "Verspätung": dep_delay})
        if arr_delay > 0:
            records.append({"Bereich": "Ankunft", "Grund": arr_reason or dep_reason or "Unbekannt", "Verspätung": arr_delay})

    if not records:
        return pd.DataFrame(columns=["Bereich", "Grund", "Anzahl", "Ø Verspätung"])

    reason_df = pd.DataFrame(records)
    result = (
        reason_df.groupby(["Bereich", "Grund"], as_index=False)
        .agg(Anzahl=("Grund", "size"), avg_delay=("Verspätung", "mean"))
        .sort_values(["Bereich", "Anzahl", "avg_delay"], ascending=[True, False, False])
    )
    result["Ø Verspätung"] = result["avg_delay"].apply(lambda x: int(float(x)) if pd.notna(x) else 0)
    return result.drop(columns=["avg_delay"])


# ---------------------------------------------------------------------------
# "Heute" cards
# ---------------------------------------------------------------------------
def _train_card(row: pd.Series) -> str:
    name = html.escape(str(row["train_key"]))
    planned = f"{row['planned_departure']:%H:%M}" if pd.notna(row["planned_departure"]) else ""
    planned_arr = row.get("planned_arrival")
    actual_arr = row.get("actual_arrival")
    if bool(row["canceled"]):
        value, status, chip_text = "Ausfall", "critical", "Ausfall"
        detail = html.escape(str(row.get("arrival_reason") or row.get("departure_reason") or ""))[:60]
    elif bool(row["arrival_observed"]):
        delay = int(float(row["arrival_delay_minutes"]))
        status = _delay_status(delay)
        value = "pünktlich" if delay <= 0 else f"+{delay} min"
        chip_text = STATUS_LABELS[status]
        detail = (
            f"an {actual_arr:%H:%M} statt {planned_arr:%H:%M}"
            if pd.notna(actual_arr) and pd.notna(planned_arr) else ""
        )
    elif bool(row.get("effective_arrival_open", False)):
        value, status, chip_text = "unterwegs", None, "noch nicht angekommen"
        detail = f"Ankunft plan {planned_arr:%H:%M}" if pd.notna(planned_arr) else ""
    else:
        value, status, chip_text = "keine Info", None, "Ankunft nicht erfasst"
        detail = ""
    chip = (
        _status_chip(status, chip_text) if status
        else f'<span class="bm-status bm-muted">{html.escape(chip_text)}</span>'
    )
    return (
        f'<div class="bm-card"><div class="bm-head"><span>{name}</span>'
        f'<span class="bm-muted">ab {planned}</span></div>'
        f'<div class="bm-value">{html.escape(value)}</div>{chip}'
        f'<div class="bm-muted">{detail}</div></div>'
    )


def _car_card(car_df: pd.DataFrame, route_label: str, day: date, train_minutes: tuple[str, int] | None) -> str:
    series = _build_car_commute_series(car_df)
    series = series[series["route_label"] == route_label]
    if series.empty:
        return ""
    today_row = series[series["service_date"] == day]
    typical = _weekday_mean(series)
    car_route = next(r for r in load_car_routes() if r.label == CAR_ROUTE_BY_TRAIN_ROUTE[route_label])
    head = f'<div class="bm-head"><span>🚗 Auto</span><span class="bm-muted">ab {car_route.target_departure:%H:%M}</span></div>'
    if today_row.empty:
        if typical is None:
            return ""
        body = f'<div class="bm-value">Ø {typical:.0f} min</div><div class="bm-muted">Werktage, kein Wert für diesen Tag</div>'
    else:
        minutes = int(today_row.iloc[0]["auto_minutes"])
        kind = "Live-Messung" if today_row.iloc[0]["source"] == "live" else "Prognose (Pendelverkehr)"
        body = f'<div class="bm-value">{minutes} min</div><div class="bm-muted">{kind}</div>'
        if train_minutes:
            train_name, total = train_minutes
            diff = total - minutes
            verdict = f"Auto {abs(diff)} min schneller" if diff > 0 else f"Zug {abs(diff)} min schneller" if diff < 0 else "gleich schnell"
            body += f'<div class="bm-muted">{html.escape(train_name)}: {total} min · {verdict}</div>'
    return f'<div class="bm-card">{head}{body}</div>'


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def _refund_card(db_path: str, today: date) -> str:
    """Refund accrued so far this month (both routes; DB pays per month)."""
    start, end = _month_bounds(today)
    try:
        cases, _, _ = claim_report.read_month(Path(db_path), start, end)
    except sqlite3.Error:
        return ""
    est = claim_report.estimate_compensation(cases)
    eur = claim_report.format_eur
    partial = sum(
        1 for c in cases if not c.canceled and c.delay_minutes is not None and 20 <= c.delay_minutes < 60
    )
    details = [f"{est.full_cases}× ≥60 · {partial}× 20–59 min"]
    if est.cap is not None:
        details.append(
            "Obergrenze erreicht" if est.total >= est.cap else f"Obergrenze {eur(est.cap)}"
        )
    if est.canceled_open:
        details.append(f"{est.canceled_open} Ausfall prüfen" if est.canceled_open == 1
                       else f"{est.canceled_open} Ausfälle prüfen")
    return (
        f'<div class="bm-card"><div class="bm-head"><span>💶 Erstattung</span>'
        f'<span class="bm-muted">{today:%m/%Y}</span></div>'
        f'<div class="bm-value">{eur(est.payable)}</div>'
        f'<div class="bm-muted">bisher angelaufen (Schätzung)</div>'
        + "".join(f'<div class="bm-muted">{html.escape(d)}</div>' for d in details)
        + "</div>"
    )


def render_today(
    df: pd.DataFrame, car_df: pd.DataFrame, route_label: str, timezone: str, db_path: str
) -> None:
    route_df = df[df["route_label"] == route_label]
    if route_df.empty:
        return
    today = datetime.now(ZoneInfo(timezone)).date()
    day = today if (route_df["service_date"] == today).any() else route_df["service_date"].max()
    day_df = route_df[route_df["service_date"] == day].sort_values("planned_departure")
    priority = PRIORITY_TRAINS.get(route_label, [])
    shown = day_df[day_df["train_key"].isin(priority)]
    if shown.empty:
        shown = day_df.head(3)

    train_minutes = None
    for _, row in shown.iterrows():
        if bool(row["arrival_observed"]) and not bool(row["canceled"]) and pd.notna(row["planned_arrival"]):
            planned_trip = (row["planned_arrival"] - row["planned_departure"]).total_seconds() / 60
            train_minutes = (str(row["train_key"]), int(round(planned_trip + float(row["arrival_delay_minutes"]))))
            break

    title = "Heute" if day == today else f"Zuletzt erfasst: {WEEKDAYS[day.weekday()]} {day:%d.%m.}"
    st.markdown(f"**{title}**")
    cards = "".join(_train_card(row) for _, row in shown.iterrows())
    cards += _car_card(car_df, route_label, day, train_minutes)
    cards += _refund_card(db_path, today)
    st.markdown(f'<div class="bm-cards">{cards}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Reliability per train (last 30 days)
# ---------------------------------------------------------------------------
def render_reliability(df: pd.DataFrame, route_label: str, end_date: date, days: int = 30) -> None:
    start_date = end_date - timedelta(days=days - 1)
    window = df[
        (df["route_label"] == route_label)
        & (df["service_date"] >= start_date)
        & (df["service_date"] <= end_date)
    ].copy()
    window["status"] = [
        _delay_status(delay if observed else None, canceled)
        for delay, observed, canceled in zip(
            window["arrival_delay_minutes"], window["arrival_observed"], window["canceled"]
        )
    ]
    window = window[window["status"].notna()]
    if window.empty:
        return

    counts = window.groupby(["zug", "status"]).size().unstack(fill_value=0)
    counts = counts.reindex(columns=STATUS_ORDER, fill_value=0)
    totals = counts.sum(axis=1)
    order = window.groupby("zug")["typical_hhmm"].first().sort_values(ascending=False, kind="stable")
    counts, totals = counts.loc[order.index], totals.loc[order.index]
    shares = counts.div(totals, axis=0) * 100
    # Show the sample size: "100 %" from two weekend days must not look like a fact.
    labels = [f"{zug}  ({int(n)} T.)" for zug, n in totals.items()]
    shares.index = labels

    fig = go.Figure()
    for status in STATUS_ORDER:
        fig.add_trace(
            go.Bar(
                y=shares.index,
                x=shares[status],
                orientation="h",
                name=STATUS_LABELS[status],
                marker=dict(color=STATUS_COLORS[status], line=dict(width=0)),
                text=[f"{v:.0f} %" if v >= 12 else "" for v in shares[status]],
                textposition="inside",
                insidetextanchor="start",
                textfont=dict(color=STATUS_INK[status], size=12),
                customdata=list(zip(counts[status], totals)),
                hovertemplate="<b>%{y}</b><br>" + STATUS_LABELS[status]
                + ": %{customdata[0]} von %{customdata[1]} Tagen (%{x:.0f} %)<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        bargap=0.35,
        height=70 + 34 * len(shares),
        margin=dict(l=10, r=10, t=36, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title=None,
                    traceorder="normal"),
        xaxis=dict(range=[0, 100], ticksuffix=" %", showgrid=True, gridcolor="rgba(128,128,128,0.18)"),
        yaxis=dict(title=None),
        hoverlabel=dict(namelength=0),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"reliability-{route_label}")


# ---------------------------------------------------------------------------
# Claims tab: same selection as the monthly claim mail
# ---------------------------------------------------------------------------
def render_claims(db_path: str, end_date: date) -> None:
    month_start = end_date.replace(day=1)
    months = [month_start]
    for _ in range(5):
        months.append((months[-1] - timedelta(days=1)).replace(day=1))
    chosen = st.selectbox(
        "Monat", months, format_func=lambda d: f"{d:%m/%Y}", key="claims-month"
    )
    month_end = (chosen.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    cases, available_from, available_through = claim_report.read_month(Path(db_path), chosen, month_end)

    full = [c for c in cases if c.delay_minutes is not None and c.delay_minutes >= 60]
    partial = [c for c in cases if c.delay_minutes is not None and 20 <= c.delay_minutes < 60]
    canceled = [c for c in cases if c.canceled]
    estimate = claim_report.estimate_compensation(cases)
    running = chosen == month_start and end_date >= date.today() - timedelta(days=1)

    c0, c1, c2, c3 = st.columns([1.4, 1, 1, 1])
    c0.metric(
        "Bisher angelaufen" if running else "Erstattung (Schätzung)",
        claim_report.format_eur(estimate.payable),
        help=(
            f"{estimate.full_cases} × ab 60 min à {claim_report.format_eur(estimate.rate)} + "
            f"{estimate.collected_minutes} gesammelte Minuten aus Fällen ab 20 min "
            f"(≈ {estimate.collected_units} × {claim_report.format_eur(estimate.rate)}). "
            "Ausfälle sind nicht eingerechnet."
        ),
    )
    c1.metric("≥60 min", len(full))
    c2.metric("20–59 min", len(partial), help=f"Zusammen {estimate.collected_minutes} Minuten Verspätung.")
    c3.metric("Ausfälle (prüfen)", len(canceled))

    if not cases:
        st.info("Keine reklamierbaren Fälle in diesem Monat erfasst.")
    else:
        routes = {
            "Morning Freiburg->Offenburg": "Freiburg → Offenburg",
            "Afternoon Offenburg->Freiburg": "Offenburg → Freiburg",
        }
        table = pd.DataFrame(
            [
                {
                    "Datum": _day_label(date.fromisoformat(c.service_date)),
                    "Zug": c.train_name,
                    "Strecke": routes.get(c.route_label, c.route_label),
                    "Ab (plan)": _hhmm(c.planned_departure),
                    "An (plan)": _hhmm(c.planned_arrival),
                    "An (ist)": _hhmm(c.actual_arrival),
                    "Verspätung": "Ausfall" if c.canceled else f"+{c.delay_minutes} min",
                }
                for c in cases
            ]
        )
        st.dataframe(
            table.style.map(_style_claim_delay, subset=["Verspätung"]),
            use_container_width=True,
            hide_index=True,
            height=38 + 35 * len(table),
        )
    trains = ", ".join(sorted({train for _, train in claim_report.TRAIN_FILTERS}))
    cap_note = (
        f" Obergrenze 25 % des Zeitkartenwerts: {claim_report.format_eur(estimate.cap)}."
        if estimate.cap is not None else ""
    )
    st.caption(
        f"Gleiche Auswahl wie die Monatsmail ({trains}; pro Strecke und Tag nur der am stärksten "
        "verspätete Zug). Die Mail geht am 1. des Folgemonats um 08:00 raus. "
        f"Schätzung für {estimate.tariff_label}: Pauschale je Fall ab 60 min; Fälle ab 20 min "
        f"werden gesammelt, je 60 gesammelte Minuten eine Pauschale (Auslegung, von der DB nicht "
        f"genau festgelegt).{cap_note} Datenbestand: {available_from or '–'} bis {available_through or '–'}."
    )

    st.markdown("**Letzte Monate**")
    history = []
    for month in months:
        m_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        m_cases, _, _ = claim_report.read_month(Path(db_path), month, m_end)
        m_est = claim_report.estimate_compensation(m_cases)
        history.append(
            {
                "Monat": f"{month:%m/%Y}",
                "≥60 min": m_est.full_cases,
                "20–59 min": sum(
                    1 for c in m_cases
                    if not c.canceled and c.delay_minutes is not None and 20 <= c.delay_minutes < 60
                ),
                "Ausfälle": m_est.canceled_open,
                "Erstattung (Schätzung)": claim_report.format_eur(m_est.payable),
            }
        )
    st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)


def _day_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m.}"


def _hhmm(raw: str | None) -> str:
    if not raw:
        return "–"
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return "–"


def _style_claim_delay(value: object) -> str:
    text = str(value or "")
    if text == "Ausfall":
        return _style_day_cell("Ausfall")
    match = re.search(r"\+(\d+)", text)
    return _style_day_cell(match.group(1)) if match else ""


# ---------------------------------------------------------------------------
# System tab
# ---------------------------------------------------------------------------
def render_car_summary(car_df: pd.DataFrame) -> None:
    st.subheader("Auto-Fahrzeit (Pendelzeiten)")
    series = _build_car_commute_series(car_df)
    if series.empty:
        st.info("Noch keine Auto-Daten. `TOMTOM_API_KEY` setzen.")
        return
    cols = st.columns(len(ROUTE_ORDER))
    for col, route_label in zip(cols, ROUTE_ORDER):
        route_series = series[series["route_label"] == route_label]
        typical = _weekday_mean(route_series)
        if route_series.empty or typical is None:
            col.metric(ROUTE_TITLES[route_label], "k.A.")
            continue
        latest = route_series.sort_values("service_date").iloc[-1]
        kind = "live" if latest["source"] == "live" else "Prognose"
        col.metric(
            ROUTE_TITLES[route_label],
            f"Ø {typical:.0f} min",
            f"{latest['service_date']:%d.%m.}: {latest['auto_minutes']} min ({kind})",
            delta_color="off",
        )
    st.caption(
        "Ø über Werktage. Läuft die Datensammlung zur Pendelzeit, wird live gemessen, "
        "sonst die TomTom-Prognose für genau diese Abfahrtszeit gespeichert."
    )


def render_combined_train_chart(df: pd.DataFrame, route_label: str, timezone: str) -> None:
    route_df = df[df["route_label"] == route_label].copy()
    if route_df.empty:
        return

    trains = (
        route_df.groupby("zug", as_index=False)
        .agg(departure_hhmm=("typical_hhmm", "first"))
        .sort_values(by=["departure_hhmm", "zug"], kind="stable")
    )

    all_trains = trains["zug"].tolist()
    priority = PRIORITY_TRAINS.get(route_label, [])
    default_trains = [z for z in all_trains if z.split(" | ", 1)[0] in priority]
    if not default_trains:
        default_trains = all_trains

    selected_trains = st.multiselect(
        "Züge in Grafik",
        options=all_trains,
        default=default_trains,
        key=f"trains-select-{route_label}",
    )

    if not selected_trains:
        st.info("Bitte mindestens einen Zug auswählen.")
        return

    today_str = datetime.now(ZoneInfo(timezone)).date().isoformat()
    fig = go.Figure()

    for train in selected_trains:
        train_df = route_df[route_df["zug"] == train]
        history = _build_train_history(train_df)

        fig.add_trace(
            go.Scatter(
                x=history["service_date"],
                y=history["arrival_delay"],
                mode="lines+markers",
                name=train,
                legendgroup=train,
                marker=dict(size=5),
                hovertemplate=f"<b>{train}</b><br>%{{x|%d.%m.%Y}}: %{{y}} min<extra></extra>",
            )
        )

        canceled_pts = history[history["canceled"]]
        if not canceled_pts.empty:
            fig.add_trace(
                go.Scatter(
                    x=canceled_pts["service_date"],
                    y=[0] * len(canceled_pts),
                    mode="markers",
                    showlegend=False,
                    legendgroup=train,
                    marker=dict(color="#7b1fa2", size=10, symbol="x"),
                    hovertemplate=f"<b>{train}</b> Ausfall<extra></extra>",
                )
            )

    fig.add_shape(
        type="line",
        x0=today_str,
        x1=today_str,
        y0=0,
        y1=1,
        xref="x",
        yref="paper",
        line=dict(dash="dash", color="rgba(100,100,100,0.45)"),
    )
    fig.add_annotation(
        x=today_str,
        y=1,
        xref="x",
        yref="paper",
        text="Heute",
        showarrow=False,
        xanchor="left",
        yanchor="top",
        font=dict(color="rgba(100,100,100,0.7)", size=11),
    )

    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Ankunfts-Verspätung (min)",
        height=360,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True, key=f"combined-chart-{route_label}")

    reason_tables = []
    for train in selected_trains:
        reason_stats = _reason_stats(route_df[route_df["zug"] == train])
        if not reason_stats.empty:
            reason_tables.append(reason_stats.assign(Zug=train))
    st.markdown("**Verspätungsgründe** (laut DB)")
    if reason_tables:
        reasons = pd.concat(reason_tables)[["Zug", "Bereich", "Grund", "Anzahl", "Ø Verspätung"]]
        st.dataframe(reasons, use_container_width=True, hide_index=True)
    else:
        st.caption("Keine Gründe gemeldet.")


def _filter_display_windows(df: pd.DataFrame) -> pd.DataFrame:
    keep = pd.Series(True, index=df.index)
    for route_label, (start, end) in DISPLAY_WINDOWS.items():
        on_route = df["route_label"] == route_label
        in_window = df["typical_hhmm"].between(start, end)
        keep &= ~on_route | in_window
    return df[keep]


def _render_data_freshness(df: pd.DataFrame, route_label: str, timezone: str) -> None:
    route_df = df[df["route_label"] == route_label]
    if route_df.empty:
        return
    last_obs = route_df["observation_ts"].max()
    last_day = route_df["service_date"].max()
    today = datetime.now(ZoneInfo(timezone)).date()
    stale = (today - last_day).days > 1
    text = f"Datenstand: zuletzt erfasst {last_obs:%d.%m. %H:%M}" if pd.notna(last_obs) else "Datenstand: unbekannt"
    if stale:
        st.warning(f"{text} – seit {last_day:%d.%m.} keine neuen Fahrten gespeichert. Datensammlung prüfen.")
    else:
        st.caption(text)


def main() -> None:
    st.set_page_config(
        page_title="DB Pünktlichkeitsmonitor",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(PAGE_CSS, unsafe_allow_html=True)

    settings = load_settings()
    db_file = Path(settings.database_path)
    if not db_file.exists():
        st.error(f"Datenbank nicht gefunden: `{settings.database_path}`")
        return
    db_mtime = db_file.stat().st_mtime
    try:
        df = _load_data_cached(settings.database_path, settings.timezone, db_mtime)
        car_df = _load_car_data_cached(settings.database_path, db_mtime)
    except sqlite3.Error as exc:
        st.error(f"Datenbank konnte nicht gelesen werden: {exc}")
        return

    if df.empty:
        st.info("Noch keine Daten vorhanden. Erst `python run_collection.py` ausführen.")
        return

    max_date = max(df["service_date"])

    with st.sidebar:
        st.markdown("### 🚆 DB Pünktlichkeitsmonitor")
        end_date = st.date_input("Berichts-Enddatum", value=max_date)
        show_all_trains = st.toggle(
            "Alle erfassten Züge anzeigen",
            value=False,
            help="Standard: nur Pendelfenster 06:30–07:30 (morgens) und 15:30–17:30 (abends).",
        )
        detailed = st.toggle(
            "Details in der Tabelle",
            value=False,
            help="Zusätzlich Abfahrtsverspätung (S) und tatsächliche Ankunftszeit zeigen.",
        )
        st.markdown("**Ankunftsverspätung**")
        st.markdown(_legend_html(), unsafe_allow_html=True)
        st.caption("… unterwegs · ? keine Ankunft erfasst · leer = keine Daten")

    full_df = df
    if not show_all_trains:
        df = _filter_display_windows(df)

    tab_morning, tab_evening, tab_claims, tab_system = st.tabs(
        ["🌅 Morgen", "🌆 Abend", "💶 Erstattung", "⚙️ System"]
    )

    for route_label, tab in zip(ROUTE_ORDER, [tab_morning, tab_evening]):
        with tab:
            _render_data_freshness(full_df, route_label, settings.timezone)
            render_today(df, car_df, route_label, settings.timezone, settings.database_path)

            matrix, day_cols, weekend_cols = build_route_matrix(
                df, route_label=route_label, end_date=end_date, days=30, detailed=detailed
            )
            if matrix.empty:
                st.write("Keine Daten für die letzten 30 Tage vorhanden.")
                continue
            st.markdown("**Letzte 30 Tage** · Ankunftsverspätung in Minuten")
            matrix_display = matrix.set_index("Zug")
            st.dataframe(
                style_matrix(matrix_display, day_cols, weekend_cols),
                use_container_width=True,
                height=min(38 + 35 * len(matrix_display), 600),
            )
            st.caption(
                "Pünktl. = Anteil der Ankünfte unter 5 min · Ø = mittlere Ankunftsverspätung · "
                "Reklam. = Tage mit ≥20 min Verspätung oder Ausfall"
            )

            st.markdown("**Zuverlässigkeit je Zug** · letzte 30 Tage, in Klammern die Zahl der Tage mit Daten")
            render_reliability(df, route_label, end_date)

            with st.expander("📈 Verlauf über alle Daten und Verspätungsgründe"):
                render_combined_train_chart(df, route_label, settings.timezone)

    with tab_claims:
        render_claims(settings.database_path, end_date)

    with tab_system:
        render_car_summary(car_df)
        st.subheader("Systemstatus")
        last_obs = full_df["observation_ts"].max()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Letztes Update", f"{last_obs:%d.%m. %H:%M}" if pd.notna(last_obs) else "-")
        c2.metric("Datensätze gesamt", int(len(full_df)))
        c3.metric("Ankunft ohne Info", int(full_df["effective_arrival_missing"].sum()))
        c4.metric("Ankunft offen", int(full_df["effective_arrival_open"].sum()))


if __name__ == "__main__":
    main()
