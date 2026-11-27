# bahn-monitor

DB-Zugpünktlichkeit Freiburg ↔ Offenburg. SQLite + Streamlit.
**Pfad:** `./`
**Remote:** https://github.com/schekoni/bahn
**Git-Identity:** schekoni (GitHub noreply)

## Dateien
| Datei | Zweck |
|---|---|
| `db_monitor/client.py` | DB REST API (OAuth2, Retry) |
| `db_monitor/openboards_collector.py` | Beobachtungen sammeln (Standard, DB OpenBoards API) |
| `db_monitor/collector.py` | Alter Sammler (Timetables API, `DB_API_PROVIDER=timetables`) |
| `db_monitor/car_collector.py` | Auto-Fahrtzeiten (TomTom/ORS) |
| `db_monitor/config.py` | Settings via .env + dataclasses |
| `db_monitor/models.py` | PlannedStop / Observation / CarObservation |
| `db_monitor/parser.py` | XML → Python (DB Timetables API) |
| `db_monitor/storage.py` | SQLite upsert-Logik |
| `run_collection.py` | Cron-Einstiegspunkt |
| `dashboard.py` | Streamlit-Dashboard |
| `monthly_claim_report.py` | Monatsmail für Fahrgastrechte (1. des Monats, `--dry-run` zum Testen) |
| `tests/` | `python -m unittest discover -s tests` |

## Befehle
```bash
source .venv/bin/activate
python run_collection.py          # Daten sammeln
streamlit run dashboard.py        # Dashboard starten
```

## Wichtige Details
- EVA Freiburg=8000107, Offenburg=8000290
- Sammelfenster: morgens 06:00–08:00, abends 15:30–17:30 (Workflow-Env)
- Dashboard zeigt standardmäßig nur 06:30–07:30 / 15:30–17:30 (`DISPLAY_WINDOWS`)
- DB: `data/train_punctuality.db`, Backups: `data/backups/`
- Dashboard-Tabs: 🌅 Morgen / 🌆 Abend / ⚙️ System
- `db_monitor/storage.py` upsert: arrival_* nur updaten wenn `arrival_observed=1`
- Backup-Retention: Datum aus Dateiname parsen (nicht st_mtime — shutil.copy2 kopiert Mtime)

## OpenBoards-Fallstricke
- Richtung per `journeyId`: Zug muss auf Abfahrtstafel Quelle UND Ankunftstafel Ziel stehen. `via` ist auf 10 Halte gekappt → Regionalzüge fehlen sonst.
- API filtert nach Echtzeit, nicht Plan → weit über das Fenster hinaus abfragen, dann nach `timeSchedule` filtern.
- Jeder Lauf trägt den Vortag nach (`BACKFILL_DAYS`, Standard 1); Läufe vor 04:00 nur den Vortag.
- Leere Strecke / API-Fehler → Exit 1 (GitHub-Fehlermail); DB wird trotzdem committet.
- ICE73 und ICE202 fahren seit 23.11.2026 nicht (Fahrplanänderung, kein Bug).

## Lokal
- Ordner liegt in iCloud-synchronisierten Dokumenten: `.venv`-Dateien werden ausgelagert (erster Start hängt), Git-Operationen können abbrechen, Finder legt „Datei 2“-Kopien an.
- Streamlit starten mit `.venv/bin/python -m streamlit run dashboard.py` (die `.venv/bin/streamlit`-Shebang zeigt auf einen alten Pfad).
