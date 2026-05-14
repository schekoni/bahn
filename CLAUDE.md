# bahn-monitor

DB-Zugpünktlichkeit Freiburg ↔ Offenburg. SQLite + Streamlit.
**Pfad:** `/Users/konradwhittaker/Documents/Projekte/bahn-monitor`
**Remote:** https://github.com/schekoni/bahn
**Git-Identity:** schekoni@gmail.com / Konrad Whittaker

## Dateien
| Datei | Zweck |
|---|---|
| `db_monitor/client.py` | DB REST API (OAuth2, Retry) |
| `db_monitor/collector.py` | Beobachtungen sammeln |
| `db_monitor/car_collector.py` | Auto-Fahrtzeiten (TomTom/ORS) |
| `db_monitor/config.py` | Settings via .env + dataclasses |
| `db_monitor/models.py` | PlannedStop / Observation / CarObservation |
| `db_monitor/parser.py` | XML → Python (DB Timetables API) |
| `db_monitor/storage.py` | SQLite upsert-Logik |
| `run_collection.py` | Cron-Einstiegspunkt |
| `dashboard.py` | Streamlit-Dashboard |

## Befehle
```bash
source .venv/bin/activate
python run_collection.py          # Daten sammeln
streamlit run dashboard.py        # Dashboard starten
```

## Wichtige Details
- EVA Freiburg=8000107, Offenburg=8000290
- Morgens 06:00–08:00, Abends 15:30–17:30
- DB: `data/train_punctuality.db`, Backups: `data/backups/`
- Dashboard-Tabs: 🌅 Morgen / 🌆 Abend / ⚙️ System
- `db_monitor/storage.py` upsert: arrival_* nur updaten wenn `arrival_observed=1`
- Backup-Retention: Datum aus Dateiname parsen (nicht st_mtime — shutil.copy2 kopiert Mtime)
