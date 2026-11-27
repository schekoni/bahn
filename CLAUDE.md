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

## Auto-Vergleich
- Live-Messung nur bis 30 min nach der Abfahrtszeit (06:30 / 16:30), sonst TomTom-Prognose (`departAt` = genau diese Zeit, historischer Pendelverkehr). Spalte `car_observations.source` = `live` | `forecast`; live ersetzt Prognose, nie umgekehrt.
- Nur Mo–Fr. Wohnadresse ist privat: `CAR_HOME_LAT/LON` in `.env` bzw. GitHub-Secrets, im Code nur Fallback Freiburg Hbf.

## Dashboard
- Farben = Erstattungsschwellen: <5 grün, 5–19 gelb, 20–59 orange (reklamierbar), ≥60/Ausfall rot. Statuspalette fest, immer mit Textlabel.
- Tab „💶 Erstattung“ nutzt `monthly_claim_report.read_month` + `estimate_compensation` → identisch mit der Monatsmail.
- Monatsmail-Züge: ICE376/ECE8 morgens, ICE73/ICE373/RE5339 abends; pro Strecke und Tag nur der am stärksten verspätete Fall (`keep_one_case_per_commute`).
- Erstattung (bahn.de): Pauschale je Fall ab 60 min (FV 2. Kl. 5 €, 1. Kl. 7,50 €; NV 1,50/2,25 €; BC100 10/15 €), max. 25 % des Kartenwerts, Auszahlung ab 4 €. Fälle ab 20 min sammelbar (nicht BC100); Umrechnung nicht festgelegt → Schätzung „je 60 gesammelte Minuten eine Pauschale“. `CLAIM_TARIFF` / `CLAIM_TICKET_PRICE`.

## Lokal
- Arbeitskopie: `~/Developer/bahn-monitor` (außerhalb von iCloud; früher unter Dokumente → iCloud brach Git-Operationen ab und lagerte die `.venv` aus).
- Remote per SSH (`git@github.com:schekoni/bahn.git`); im Schlüsselbund ist kein HTTPS-Login gespeichert.
- `data/train_punctuality.db` wird von GitHub Actions committet: vor lokalen Sammelläufen `git pull`, lokale DB-Änderungen nur bewusst pushen (binär, nicht mergebar).
