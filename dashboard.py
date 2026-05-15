from __future__ import annotations

import re
import sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db_monitor.config import load_settings


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

SUMMARY_COLS = ["Trend", "Med.", "Ø", "Ausf."]

PRIORITY_TRAINS = {
    "Morning Freiburg->Offenburg": ["ECE8", "ICE376"],
    "Afternoon Offenburg->Freiburg": ["ICE73", "ICE373", "RE5339"],
}

_LEGEND_HTML = """
<div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin:0;padding-top:.45rem">
  <span style="background:#2e7d32;color:white;padding:2px 8px;border-radius:10px;font-size:.72em;font-weight:600">&lt;5</span>
  <span style="background:#ef6c00;color:white;padding:2px 8px;border-radius:10px;font-size:.72em;font-weight:600">5–15</span>
  <span style="background:#c62828;color:white;padding:2px 8px;border-radius:10px;font-size:.72em;font-weight:600">&gt;15</span>
  <span style="background:#7b1fa2;color:white;padding:2px 8px;border-radius:10px;font-size:.72em;font-weight:600">Ausfall</span>
  <span style="color:#607d8b;font-size:.7em;margin-left:4px">min · S=Abfahrt A=Ankunft</span>
</div>
"""


def _clean_reason_text(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null", "nan", "-"}:
        return ""
    text = re.sub(r"\b(?:none|null|nan)\b", "", text, flags=re.IGNORECASE)
    text = text.replace("||", "|").strip(" |")
    parts = [p.strip() for p in text.split("|")]
    cleaned = [p for p in parts if p and p.lower() not in {"none", "null", "nan", "-"}]
    return " | ".join(cleaned)


def _clean_label_text(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null", "nan", "-"}:
        return ""
    return text


def _coerce_datetime_naive(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    if isinstance(parsed.dtype, pd.DatetimeTZDtype):
        return parsed.dt.tz_localize(None)
    if parsed.dtype.kind == "M":
        return parsed

    def _to_naive(value: object) -> pd.Timestamp | pd.NaT:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return pd.NaT
        if getattr(ts, "tzinfo", None) is not None:
            try:
                return ts.tz_localize(None)
            except TypeError:
                return ts.tz_convert(None)
        return ts

    return pd.to_datetime(series.map(_to_naive), errors="coerce")


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
            df[col] = _coerce_datetime_naive(df[col])

    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    df["canceled"] = df["canceled"].astype(bool)
    df["arrival_observed"] = df["arrival_observed"].astype(bool)
    df["arrival_info_missing"] = df["arrival_info_missing"].astype(bool)
    df["train_name"] = df["train_name"].map(_clean_label_text)
    df["line"] = df["line"].map(_clean_label_text)
    df["departure_reason"] = df["departure_reason"].map(_clean_reason_text)
    df["arrival_reason"] = df["arrival_reason"].map(_clean_reason_text)
    df["departure_hhmm"] = df["planned_departure"].dt.strftime("%H:%M")
    df["zug"] = df.apply(
        lambda r: f"{(r['train_name'] or r['line'] or 'Unbekannt')} | {r['departure_hhmm']}",
        axis=1,
    )

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
        df = pd.read_sql_query(
            """
            SELECT
                service_date,
                route_label,
                observation_ts,
                target_departure_time,
                duration_minutes,
                distance_km
            FROM car_observations
            ORDER BY service_date DESC, route_label
            """,
            con,
            parse_dates=["observation_ts"],
        )
    if df.empty:
        return df
    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    return df


def _build_car_commute_series(car_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for train_route, car_route in CAR_ROUTE_BY_TRAIN_ROUTE.items():
        tmp = car_df[car_df["route_label"] == car_route].copy()
        if tmp.empty:
            continue
        for service_date, day in tmp.groupby("service_date"):
            chosen = day.sort_values("observation_ts", ascending=False, kind="stable").iloc[0]
            rows.append(
                {
                    "service_date": service_date,
                    "route_label": train_route,
                    "auto_minutes": int(chosen["duration_minutes"]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["service_date", "route_label", "auto_minutes"])
    return pd.DataFrame(rows)


def _cell_value(row: pd.Series) -> str:
    if bool(row["canceled"]) or bool(row.get("effective_arrival_missing", False)):
        return "Ausfall"
    dep_token = "-" if bool(row["effective_departure_unknown"]) else str(int(float(row["delay_minutes"])))
    if bool(row["arrival_observed"]):
        arr = int(float(row["arrival_delay_minutes"]))
        actual_arrival = row.get("actual_arrival")
        if pd.notna(actual_arrival):
            arrival_time = pd.to_datetime(actual_arrival).strftime("%H:%M")
            return f"S:{dep_token} A:{arr} ({arrival_time})"
        return f"S:{dep_token} A:{arr}"
    return f"S:{dep_token} A:-"


def _delay_color(delay: float) -> str:
    if delay < 5:
        return "#2e7d32"
    if delay <= 15:
        return "#ef6c00"
    return "#c62828"


def _style_day_cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value)
    if not text:
        return ""
    if "Ausfall" in text:
        return "background-color: #7b1fa2; color: white; font-weight: 600;"
    match = re.search(r"S:(-|\d+)\s+A:(-|\d+)", text)
    if not match:
        return ""
    dep_token = match.group(1)
    arr_token = match.group(2)
    levels: list[int] = []
    if re.fullmatch(r"\d+", dep_token):
        levels.append(int(dep_token))
    if re.fullmatch(r"\d+", arr_token):
        levels.append(int(arr_token))
    if not levels:
        return ""
    level = max(levels)
    color = _delay_color(level)
    text_color = "white" if color in {"#2e7d32", "#c62828"} else "black"
    return f"background-color: {color}; color: {text_color}; font-weight: 600;"


def _style_weekend_day_cell(value: object) -> str:
    base = _style_day_cell(value)
    border = "border-left: 3px solid #90a4ae;"
    return f"{base} {border}" if base else f"background-color: #f0f4f8; {border}"


def _style_trend_cell(value: object) -> str:
    text = str(value or "")
    if text == "↓":
        return "color: #2e7d32; font-weight: 700;"
    if text == "↑":
        return "color: #c62828; font-weight: 700;"
    return "color: #90a4ae; font-weight: 600;"


def _style_metric_cell(value: object) -> str:
    text = str(value or "")
    if text in {"-", ""}:
        return "color: #90a4ae;"
    try:
        v = int(text)
    except ValueError:
        return ""
    color = _delay_color(v)
    text_color = "white" if color in {"#2e7d32", "#c62828"} else "black"
    return f"background-color: {color}; color: {text_color}; font-weight: 600;"


def _style_ausfall_count_cell(value: object) -> str:
    text = str(value or "")
    try:
        v = int(text)
    except ValueError:
        return ""
    if v == 0:
        return "color: #90a4ae;"
    if v <= 2:
        return "color: #ef6c00; font-weight: 600;"
    return "background-color: #7b1fa2; color: white; font-weight: 600;"


def style_matrix(
    matrix: pd.DataFrame,
    day_cols: list[str],
    weekend_cols: list[str] | None = None,
) -> pd.io.formats.style.Styler:
    styler = matrix.style
    wknd = set(weekend_cols or [])
    non_wknd_day = [c for c in day_cols if c not in wknd and c in matrix.columns]
    wknd_day = [c for c in day_cols if c in wknd and c in matrix.columns]

    if non_wknd_day:
        styler = styler.map(_style_day_cell, subset=non_wknd_day)
    for wknd_col in wknd_day:
        styler = styler.map(_style_weekend_day_cell, subset=[wknd_col])

    if "Trend" in matrix.columns:
        styler = styler.map(_style_trend_cell, subset=["Trend"])
    if "Med." in matrix.columns:
        styler = styler.map(_style_metric_cell, subset=["Med."])
    if "Ø" in matrix.columns:
        styler = styler.map(_style_metric_cell, subset=["Ø"])
    if "Ausf." in matrix.columns:
        styler = styler.map(_style_ausfall_count_cell, subset=["Ausf."])

    return styler


def build_route_matrix(
    df: pd.DataFrame, route_label: str, end_date: date, days: int = 30
) -> tuple[pd.DataFrame, list[str], list[str]]:
    route_df = df[df["route_label"] == route_label].copy()
    if route_df.empty:
        return route_df, [], []

    start_date = end_date - timedelta(days=days - 1)
    route_30 = route_df[(route_df["service_date"] >= start_date) & (route_df["service_date"] <= end_date)].copy()
    if route_30.empty:
        return route_30, [], []
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    route_prev = route_df[(route_df["service_date"] >= prev_start) & (route_df["service_date"] <= prev_end)].copy()

    route_30["day_cell"] = route_30.apply(_cell_value, axis=1)
    route_30["display_ausfall"] = route_30["canceled"] | route_30.get("effective_arrival_missing", False)

    pivot = (
        route_30.pivot_table(index="zug", columns="service_date", values="day_cell", aggfunc="first")
        .sort_index(axis=1, ascending=False)
        .reset_index()
        .rename(columns={"zug": "Zug"})
    )

    metric_base = route_30.copy()
    metric_base.loc[metric_base["display_ausfall"], ["delay_minutes", "arrival_delay_minutes"]] = pd.NA
    metric_base.loc[~metric_base["arrival_observed"], ["arrival_delay_minutes"]] = pd.NA

    avg_arr_raw = metric_base.groupby("zug", dropna=False)["arrival_delay_minutes"].mean()
    avg_arr = avg_arr_raw.apply(lambda x: int(float(x)) if pd.notna(x) else pd.NA)
    median_arr = metric_base.groupby("zug", dropna=False)["arrival_delay_minutes"].median().apply(
        lambda x: int(float(x)) if pd.notna(x) else pd.NA
    )
    cancel_days = (
        route_30[route_30["display_ausfall"]]
        .groupby("zug", dropna=False)["service_date"]
        .nunique()
        .rename("ausfalltage")
    )

    prev_avg_arr_raw = pd.Series(dtype="float64")
    if not route_prev.empty:
        prev_metric_base = route_prev.copy()
        prev_metric_base["display_ausfall"] = prev_metric_base["canceled"] | prev_metric_base.get(
            "effective_arrival_missing", False
        )
        prev_metric_base.loc[prev_metric_base["display_ausfall"], ["delay_minutes", "arrival_delay_minutes"]] = pd.NA
        prev_metric_base.loc[~prev_metric_base["arrival_observed"], ["arrival_delay_minutes"]] = pd.NA
        prev_avg_arr_raw = prev_metric_base.groupby("zug", dropna=False)["arrival_delay_minutes"].mean()

    summary = pd.DataFrame(
        {
            "Zug": avg_arr.index,
            "avg_arr_raw": avg_arr_raw.values,
            "avg_arr": avg_arr.values,
            "median_arr": median_arr.values,
        }
    )
    summary = summary.merge(cancel_days.reset_index().rename(columns={"zug": "Zug"}), on="Zug", how="left")
    summary = summary.merge(
        prev_avg_arr_raw.rename("prev_avg_arr_raw").reset_index().rename(columns={"zug": "Zug"}),
        on="Zug",
        how="left",
    )
    summary["ausfalltage"] = summary["ausfalltage"].fillna(0).astype(int)

    def _trend_symbol(row: pd.Series) -> str:
        cur = row["avg_arr_raw"]
        prev = row["prev_avg_arr_raw"]
        if pd.isna(cur) or pd.isna(prev):
            return "→"
        diff = float(cur) - float(prev)
        if abs(diff) < 0.5:
            return "→"
        return "↓" if diff < 0 else "↑"

    summary["Trend"] = summary.apply(_trend_symbol, axis=1)
    summary["Med."] = summary["median_arr"].apply(lambda x: "-" if pd.isna(x) else str(int(float(x))))
    summary["Ø"] = summary["avg_arr"].apply(lambda x: "-" if pd.isna(x) else str(int(float(x))))
    summary["Ausf."] = summary["ausfalltage"].astype(str)
    summary = summary[["Zug", "Trend", "Med.", "Ø", "Ausf."]]

    result = pivot.merge(summary, on="Zug", how="left")

    meta = (
        route_30.groupby("zug", as_index=False)
        .agg(departure_hhmm=("departure_hhmm", "first"))
        .rename(columns={"zug": "Zug"})
    )
    result = result.merge(meta, on="Zug", how="left")
    result = result.sort_values(by=["departure_hhmm", "Zug"], kind="stable")

    rename_map: dict[object, str] = {}
    day_cols: list[str] = []
    weekend_cols: list[str] = []
    for col in result.columns:
        if isinstance(col, date):
            label = col.strftime("%d.%m")
            rename_map[col] = label
            day_cols.append(label)
            if col.weekday() >= 5:
                weekend_cols.append(label)
    result = result.rename(columns=rename_map)

    for col in day_cols:
        if col not in result.columns:
            continue
        result[col] = (
            result[col]
            .astype("string")
            .str.replace(r"(?i)\b(?:none|null|nan)\b", "", regex=True)
            .str.strip()
            .replace({"": pd.NA, "-": pd.NA})
            .fillna("Ausfall")
        )

    if day_cols and "Ausf." in result.columns:
        result["Ausf."] = (result[day_cols] == "Ausfall").sum(axis=1).astype(int).astype(str)

    if day_cols:
        ran_mask = ~(result[day_cols] == "Ausfall").all(axis=1)
        result = result[ran_mask].copy()

    result = result.drop(columns=["departure_hhmm"])

    stat_cols_present = [c for c in SUMMARY_COLS if c in result.columns]
    day_only_cols = [c for c in result.columns if c not in stat_cols_present and c != "Zug"]
    ordered_cols = ["Zug"] + day_only_cols + stat_cols_present
    return result[ordered_cols], day_cols, weekend_cols


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
        canceled = bool(row.get("canceled", False)) or bool(row.get("effective_arrival_missing", False))

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


def render_car_summary(car_df: pd.DataFrame) -> None:
    st.subheader("Auto-Fahrtdauer (Pendelzeiten)")
    if car_df.empty:
        st.info("Auto-Daten noch nicht verfügbar. Setze je nach Provider `TOMTOM_API_KEY` oder `ORS_API_KEY`.")
        return

    car_series = _build_car_commute_series(car_df)
    if car_series.empty:
        st.info("Noch keine Auto-Daten vorhanden.")
        return

    car_series["route_name"] = car_series["route_label"].map(ROUTE_TITLES).fillna(car_series["route_label"])
    car_series = car_series.sort_values(["service_date", "route_name"])

    latest_date = car_series["service_date"].max()
    latest = car_series[car_series["service_date"] == latest_date]
    latest_obs_ts = pd.to_datetime(car_df["observation_ts"], errors="coerce").max()
    avg_by_route = (
        car_series.groupby("route_name", as_index=False)["auto_minutes"]
        .mean()
        .rename(columns={"auto_minutes": "avg_auto_minutes"})
    )
    if pd.notna(latest_obs_ts):
        st.caption(f"Letzter Auto-Messpunkt: {latest_obs_ts.strftime('%d.%m.%Y %H:%M')}")
    else:
        st.caption(f"Letzter Auto-Messpunkt: {latest_date}")
    c1, c2 = st.columns(2)
    for col, label in ((c1, "Freiburg → Offenburg"), (c2, "Offenburg → Freiburg")):
        row_today = latest[latest["route_name"] == label]
        row_avg = avg_by_route[avg_by_route["route_name"] == label]
        if row_avg.empty:
            col.metric(label, "k.A.")
            continue
        avg_val = int(round(float(row_avg.iloc[0]["avg_auto_minutes"])))
        if row_today.empty:
            col.metric(label, f"Ø {avg_val} min")
        else:
            today_val = int(row_today.iloc[0]["auto_minutes"])
            col.metric(label, f"Ø {avg_val} min", f"Heute: {today_val} min")


def _render_route_car_metric(car_df: pd.DataFrame, route_label: str) -> None:
    car_series = _build_car_commute_series(car_df)
    if car_series.empty:
        return
    route_series = car_series[car_series["route_label"] == route_label]
    if route_series.empty:
        return

    latest_date = route_series["service_date"].max()
    latest_row = route_series[route_series["service_date"] == latest_date]
    avg_val = int(round(float(route_series["auto_minutes"].mean())))

    if not latest_row.empty:
        today_val = int(latest_row.iloc[0]["auto_minutes"])
        delta = today_val - avg_val
        date_label = latest_date.strftime("%d.%m") if hasattr(latest_date, "strftime") else str(latest_date)
        st.metric(
            f"🚗 Auto ({date_label})",
            f"{today_val} min",
            delta=f"{delta:+d} min ggü. Ø {avg_val}",
            delta_color="inverse",
        )
    else:
        st.metric("🚗 Auto", f"Ø {avg_val} min")


def render_combined_train_chart(df: pd.DataFrame, route_label: str, timezone: str) -> None:
    route_df = df[df["route_label"] == route_label].copy()
    if route_df.empty:
        return

    trains = (
        route_df.groupby("zug", as_index=False)
        .agg(departure_hhmm=("departure_hhmm", "first"))
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

    for train in selected_trains:
        train_df = route_df[route_df["zug"] == train]
        reason_stats = _reason_stats(train_df)
        if not reason_stats.empty:
            with st.expander(f"Verspätungsgründe: {train}"):
                st.dataframe(reason_stats, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="DB Pünktlichkeitsmonitor", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.2rem; padding-bottom: 1rem;}
        div[data-testid="stDateInput"] label {font-size: .75rem; margin-bottom: 0;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    settings = load_settings()
    df = load_data(settings.database_path, settings.timezone)
    car_df = load_car_data(settings.database_path)

    if df.empty:
        st.info("Noch keine Daten vorhanden. Erst `python run_collection.py` ausführen.")
        return

    max_date = max(df["service_date"])
    title_col, legend_col, date_col = st.columns([0.28, 0.48, 0.24])
    with title_col:
        st.markdown(
            "<div style='font-weight:700;font-size:1.15rem;padding-top:.45rem'>🚆 DB Pünktlichkeitsmonitor</div>",
            unsafe_allow_html=True,
        )
    with legend_col:
        st.markdown(_LEGEND_HTML, unsafe_allow_html=True)
    with date_col:
        end_date = st.date_input("Enddatum", value=max_date, label_visibility="collapsed")

    route_payloads: list[tuple[str, pd.DataFrame, list[str], list[str]]] = []
    for route_label in ROUTE_ORDER:
        matrix, day_cols, weekend_cols = build_route_matrix(
            df, route_label=route_label, end_date=end_date, days=30
        )
        route_payloads.append((route_label, matrix, day_cols, weekend_cols))

    tab_morgen, tab_abend, tab_system = st.tabs(["🌅 Morgen", "🌆 Abend", "⚙️ System"])

    for (route_label, matrix, day_cols, weekend_cols), tab in zip(route_payloads, [tab_morgen, tab_abend]):
        with tab:
            if matrix.empty:
                st.write("Keine Daten für die letzten 30 Tage vorhanden.")
                continue

            if not car_df.empty:
                car_col, _ = st.columns([0.35, 0.65])
                with car_col:
                    _render_route_car_metric(car_df, route_label)

            matrix_display = matrix.set_index("Zug")
            stat_cols = [c for c in SUMMARY_COLS if c in matrix_display.columns]
            day_view_cols = [c for c in matrix_display.columns if c not in stat_cols]

            left, right = st.columns([0.80, 0.20], gap="small")
            with left:
                st.dataframe(
                    style_matrix(matrix_display[day_view_cols], day_cols, weekend_cols),
                    use_container_width=True,
                    hide_index=False,
                )
            with right:
                st.dataframe(
                    style_matrix(matrix_display[stat_cols], [], None),
                    use_container_width=True,
                    hide_index=True,
                )

            st.markdown("**Ankunfts-Verspätung je Zug**")
            render_combined_train_chart(df, route_label, settings.timezone)

    with tab_system:
        render_car_summary(car_df)
        st.subheader("Systemstatus")
        last_obs = df["observation_ts"].max()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Letztes Update", str(last_obs)[:19] if pd.notna(last_obs) else "-")
        c2.metric("Datensätze gesamt", int(len(df)))
        c3.metric("Ankunft ohne Info", int(df["effective_arrival_missing"].sum()))
        c4.metric("Ankunft offen", int(df["effective_arrival_open"].sum()))


if __name__ == "__main__":
    main()
