from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from datetime import date, timedelta
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
    "Morning Freiburg->Offenburg": "Freiburg -> Offenburg",
    "Afternoon Offenburg->Freiburg": "Offenburg -> Freiburg",
}

COMMUTE_TARGET_TIME = {
    "Morning Freiburg->Offenburg": "06:45",
    "Afternoon Offenburg->Freiburg": "16:30",
}

CAR_ROUTE_BY_TRAIN_ROUTE = {
    "Morning Freiburg->Offenburg": "Car Morning Freiburg->Offenburg",
    "Afternoon Offenburg->Freiburg": "Car Afternoon Offenburg->Freiburg",
}


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
                parse_dates=["observation_ts", "planned_departure", "planned_arrival", "actual_arrival"],
            )
        except Exception:
            return pd.DataFrame()

    if df.empty:
        return df

    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    df["canceled"] = df["canceled"].astype(bool)
    df["arrival_observed"] = df["arrival_observed"].astype(bool)
    df["arrival_info_missing"] = df["arrival_info_missing"].astype(bool)
    df["train_name"] = df["train_name"].fillna("")
    df["line"] = df["line"].fillna("")
    df["departure_reason"] = df["departure_reason"].fillna("")
    df["arrival_reason"] = df["arrival_reason"].fillna("")
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


def render_car_summary(car_df: pd.DataFrame) -> None:
    st.subheader("Auto-Fahrtdauer (Pendelzeiten)")
    if car_df.empty:
        st.info("Auto-Daten noch nicht verfügbar. Setze `ORS_API_KEY` für openrouteservice.")
        return

    car_series = _build_car_commute_series(car_df)
    if car_series.empty:
        st.info("Noch keine Auto-Daten vorhanden.")
        return

    car_series["route_name"] = car_series["route_label"].map(ROUTE_TITLES).fillna(car_series["route_label"])
    car_series = car_series.sort_values(["service_date", "route_name"])

    latest_date = car_series["service_date"].max()
    latest = car_series[car_series["service_date"] == latest_date]
    avg_by_route = (
        car_series.groupby("route_name", as_index=False)["auto_minutes"]
        .mean()
        .rename(columns={"auto_minutes": "avg_auto_minutes"})
    )
    st.caption(f"Letzter Auto-Messpunkt: {latest_date}")
    c1, c2 = st.columns(2)
    for col, label in ((c1, "Freiburg -> Offenburg"), (c2, "Offenburg -> Freiburg")):
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


def _cell_value(row: pd.Series) -> str:
    if bool(row["canceled"]):
        return "Ausfall"
    dep = int(float(row["delay_minutes"]))
    if bool(row["arrival_observed"]):
        arr = int(float(row["arrival_delay_minutes"]))
        return f"S:{dep} A:{arr}"
    if bool(row["effective_arrival_missing"]):
        return f"S:{dep} A:k.A."
    return f"S:{dep} A:offen"


def _delay_color(delay: float) -> str:
    if delay < 5:
        return "#2e7d32"  # green
    if delay <= 15:
        return "#ef6c00"  # orange
    return "#c62828"  # red


def _style_day_cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""

    text = str(value)
    if not text:
        return ""

    if "Ausfall" in text:
        return "background-color: #7b1fa2; color: white; font-weight: 600;"

    match = re.search(r"S:(-?\d+)\s+A:([-\d]+|k\.A\.|offen)", text)
    if not match:
        return ""

    dep = int(match.group(1))
    arr_token = match.group(2)
    levels = [dep]
    if re.fullmatch(r"-?\d+", arr_token):
        levels.append(int(arr_token))
    level = max(levels)
    color = _delay_color(level)
    text_color = "white" if color in {"#2e7d32", "#c62828"} else "black"
    return f"background-color: {color}; color: {text_color}; font-weight: 600;"


def style_matrix(matrix: pd.DataFrame, day_cols: list[str]) -> pd.io.formats.style.Styler:
    styler = matrix.style
    if day_cols:
        styler = styler.map(_style_day_cell, subset=day_cols)

    if "Ø Start-Verspätung (30d)" in matrix.columns:
        styler = styler.format({"Ø Start-Verspätung (30d)": "{:.0f}"})
    if "Ø Ankunfts-Verspätung (30d)" in matrix.columns:
        styler = styler.format({"Ø Ankunfts-Verspätung (30d)": "{:.0f}"})

    return styler


def build_route_matrix(df: pd.DataFrame, route_label: str, end_date: date, days: int = 30) -> tuple[pd.DataFrame, list[str]]:
    route_df = df[df["route_label"] == route_label].copy()
    if route_df.empty:
        return route_df, []

    start_date = end_date - timedelta(days=days - 1)
    route_30 = route_df[(route_df["service_date"] >= start_date) & (route_df["service_date"] <= end_date)].copy()
    if route_30.empty:
        return route_30, []

    route_30["day_cell"] = route_30.apply(_cell_value, axis=1)

    pivot = (
        route_30.pivot_table(index="zug", columns="service_date", values="day_cell", aggfunc="first")
        .sort_index(axis=1)
        .reset_index()
        .rename(columns={"zug": "Zug"})
    )

    metric_base = route_30.copy()
    metric_base.loc[metric_base["canceled"], ["delay_minutes", "arrival_delay_minutes"]] = pd.NA
    metric_base.loc[~metric_base["arrival_observed"], ["arrival_delay_minutes"]] = pd.NA

    avg_dep = metric_base.groupby("zug", dropna=False)["delay_minutes"].mean().apply(
        lambda x: int(float(x)) if pd.notna(x) else pd.NA
    )
    avg_arr = metric_base.groupby("zug", dropna=False)["arrival_delay_minutes"].mean().apply(
        lambda x: int(float(x)) if pd.notna(x) else pd.NA
    )
    cancel_days = (
        route_30[route_30["canceled"]]
        .groupby("zug", dropna=False)["service_date"]
        .nunique()
        .rename("Ausfalltage (30d)")
    )

    summary = pd.DataFrame(
        {
            "Zug": avg_dep.index,
            "Ø Start-Verspätung (30d)": avg_dep.values,
            "Ø Ankunfts-Verspätung (30d)": avg_arr.values,
        }
    )
    summary = summary.merge(cancel_days.reset_index().rename(columns={"zug": "Zug"}), on="Zug", how="left")
    summary["Ausfalltage (30d)"] = summary["Ausfalltage (30d)"].fillna(0).astype(int)

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
    for col in result.columns:
        if isinstance(col, date):
            label = col.strftime("%d.%m")
            rename_map[col] = label
            day_cols.append(label)
    result = result.rename(columns=rename_map)

    result = result.drop(columns=["departure_hhmm"])

    summary_cols = ["Ø Start-Verspätung (30d)", "Ø Ankunfts-Verspätung (30d)", "Ausfalltage (30d)"]
    ordered_cols = [c for c in result.columns if c not in summary_cols] + summary_cols
    return result[ordered_cols], day_cols


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
        dep_reason = (row.get("departure_reason") or "").strip()
        arr_reason = (row.get("arrival_reason") or "").strip()
        dep_delay = int(float(row.get("delay_minutes", 0) or 0))
        arr_delay = int(float(row.get("arrival_delay_minutes", 0) or 0))
        canceled = bool(row.get("canceled", False))

        if canceled:
            records.append(
                {
                    "Bereich": "Start",
                    "Grund": dep_reason or "Ausfall",
                    "Verspätung": dep_delay,
                }
            )
            records.append(
                {
                    "Bereich": "Ankunft",
                    "Grund": arr_reason or dep_reason or "Ausfall",
                    "Verspätung": arr_delay,
                }
            )
            continue

        if dep_delay > 0:
            records.append(
                {
                    "Bereich": "Start",
                    "Grund": dep_reason or "Unbekannt",
                    "Verspätung": dep_delay,
                }
            )
        if arr_delay > 0:
            records.append(
                {
                    "Bereich": "Ankunft",
                    "Grund": arr_reason or dep_reason or "Unbekannt",
                    "Verspätung": arr_delay,
                }
            )

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


def render_train_expandable_charts(df: pd.DataFrame, route_label: str) -> None:
    route_df = df[df["route_label"] == route_label].copy()
    if route_df.empty:
        return

    trains = (
        route_df.groupby("zug", as_index=False)
        .agg(departure_hhmm=("departure_hhmm", "first"))
        .sort_values(by=["departure_hhmm", "zug"], kind="stable")
    )

    st.markdown("**Verlauf je Zug (alle verfügbaren Daten)**")
    for train in trains["zug"]:
        with st.expander(train):
            train_df = route_df[route_df["zug"] == train].copy()
            history = _build_train_history(train_df)

            fig = go.Figure()
            fig.add_trace(
                go.Scatter(
                    x=history["service_date"],
                    y=history["start_delay"],
                    mode="lines+markers",
                    name="Start-Verspätung",
                    line=dict(color="#1f77b4", width=2),
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=history["service_date"],
                    y=history["arrival_delay"],
                    mode="lines+markers",
                    name="Ankunfts-Verspätung",
                    line=dict(color="#ff7f0e", width=2),
                )
            )

            canceled_points = history[history["canceled"]]
            if not canceled_points.empty:
                fig.add_trace(
                    go.Scatter(
                        x=canceled_points["service_date"],
                        y=[0] * len(canceled_points),
                        mode="markers",
                        name="Ausfall",
                        marker=dict(color="#7b1fa2", size=10, symbol="x"),
                    )
                )

            fig.update_layout(
                xaxis_title="Datum",
                yaxis_title="Verspätung (Minuten)",
                height=320,
                margin=dict(l=20, r=20, t=30, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            chart_key = f"history-chart-{route_label}-{train}"
            st.plotly_chart(fig, use_container_width=True, key=chart_key)

            reason_stats = _reason_stats(train_df)
            st.markdown("**Statistik der Verspätungsgründe**")
            if reason_stats.empty:
                st.write("Keine verspätungsrelevanten Gründe in den vorhandenen Daten.")
            else:
                st.dataframe(reason_stats, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="DB Pünktlichkeitsmonitor", layout="wide")
    st.title("DB Pünktlichkeitsmonitor")
    st.caption(
        "Tagesspalten: S=Start, A=Ankunft (Minuten). A:k.A.=keine Info (zu spät abgefragt), A:offen=noch im 1h-Fenster. Farben: grün <5, orange <=15, rot >15, lila=Ausfall."
    )

    settings = load_settings()
    df = load_data(settings.database_path, settings.timezone)
    car_df = load_car_data(settings.database_path)

    if df.empty:
        st.info("Noch keine Daten vorhanden. Erst `python run_collection.py` ausführen.")
        return

    max_date = max(df["service_date"])
    end_date = st.date_input("Berichts-Enddatum", value=max_date)
    render_car_summary(car_df)

    route_payloads: list[tuple[str, pd.DataFrame, list[str]]] = []
    for route_label in ROUTE_ORDER:
        matrix, day_cols = build_route_matrix(df, route_label=route_label, end_date=end_date, days=30)
        route_payloads.append((route_label, matrix, day_cols))

    # 1) Main tables first, one below another.
    for route_label, matrix, day_cols in route_payloads:
        st.subheader(ROUTE_TITLES.get(route_label, route_label))
        if matrix.empty:
            st.write("Keine Daten für die letzten 30 Tage vorhanden.")
            continue

        styled = style_matrix(matrix, day_cols)
        st.dataframe(styled, use_container_width=True, hide_index=True)

    # 2) Then train histories, separated by route.
    for route_label, _, _ in route_payloads:
        st.subheader(f"Verlauf je Zug: {ROUTE_TITLES.get(route_label, route_label)}")
        render_train_expandable_charts(df, route_label)

    # 3) Health metrics at the end.
    st.subheader("Systemstatus")
    last_obs = df["observation_ts"].max()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Letztes Update", str(last_obs)[:19] if pd.notna(last_obs) else "k.A.")
    c2.metric("Datensätze gesamt", int(len(df)))
    c3.metric("Ankunft k.A.", int(df["effective_arrival_missing"].sum()))
    c4.metric("Ankunft offen", int(df["effective_arrival_open"].sum()))


if __name__ == "__main__":
    main()
