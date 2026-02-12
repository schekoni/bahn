# DB Puenktlichkeitsmonitor

Dieses Projekt protokolliert automatisch die Puenktlichkeit fuer folgende Zeitfenster:

- Freiburg -> Offenburg: alle Zuege zwischen 06:00 und 08:00
- Offenburg -> Freiburg: alle Zuege zwischen 15:30 und 17:30

Erfasste Kennzahlen pro Zug:

- Zugname (z.B. `ICE 123`, `RE 7`)
- Start-Verspaetung in Minuten
- Ankunfts-Verspaetung in Minuten
- Ausfallstatus

Die Daten werden aus der DB API geholt und in SQLite gespeichert. Das Dashboard wird mit Streamlit angezeigt.

## Setup

1. Python-Umgebung erstellen und Pakete installieren:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Umgebungsvariablen anlegen:

```bash
cp .env.example .env
```

3. `DB_CLIENT_ID` und `DB_API_KEY` in `.env` eintragen.
4. EVA-Nummern pruefen (Default ist gesetzt):

```env
MORNING_SOURCE_EVA=8000107
MORNING_TARGET_EVA=8000290
AFTERNOON_SOURCE_EVA=8000290
AFTERNOON_TARGET_EVA=8000107
```

## Datenlauf starten

```bash
python run_collection.py
```

## Dashboard starten

```bash
streamlit run dashboard.py
```

## Dashboard-Layout

- Zwei Tabellen: `Freiburg -> Offenburg` und `Offenburg -> Freiburg`
- Zeile pro Zug
- Tagesspalten mit `S:x A:y` (Start/Ankunfts-Verspaetung)
- Summen rechts: `Ø Start-Verspaetung (30d)`, `Ø Ankunfts-Verspaetung (30d)`, `Ausfalltage (30d)`
- Im ausklappbaren Bereich je Zug: Verlaufsgrafik ueber alle Daten und Statistik der Verspaetungsgruende

## Tägliche automatische Aktualisierung

Lokal auf macOS kannst du `launchd` verwenden (bereits eingerichtet).

## Kostenlose Cloud-Variante (ohne eigenen Server)

Dieses Repo enthaelt einen stündlichen GitHub-Workflow:
- `/Users/konradwhittaker/Documents/New project/.github/workflows/db-collector.yml`
- Trigger: stündlich nur tagsüber (UTC 05:00-20:00)
- Aufgabe: `run_collection.py` ausfuehren und `data/train_punctuality.db` ins Repo committen

Einrichtung:
1. Repo zu GitHub pushen.
2. In GitHub unter `Settings -> Secrets and variables -> Actions` diese Secrets anlegen:
`DB_CLIENT_ID`
`DB_API_KEY`
3. Unter `Actions` den Workflow `DB Collector` einmal manuell mit `Run workflow` starten.
4. Streamlit Community Cloud mit diesem Repo verbinden und `dashboard.py` als Startdatei waehlen.

Hinweis:
- Streamlit Cloud zeigt immer den zuletzt aus GitHub geladenen Stand.
- Wenn der Workflow nicht laeuft, bleiben die Daten auf dem letzten Commit-Stand.

## (Alt) Cron-Beispiel

Falls du trotzdem lokal per Cron fahren willst:

```cron
10 8 * * * cd "/Users/konradwhittaker/Documents/New project" && "/Users/konradwhittaker/Documents/New project/.venv/bin/python" run_collection.py >> "/Users/konradwhittaker/Documents/New project/cron.log" 2>&1
10 17 * * * cd "/Users/konradwhittaker/Documents/New project" && "/Users/konradwhittaker/Documents/New project/.venv/bin/python" run_collection.py >> "/Users/konradwhittaker/Documents/New project/cron.log" 2>&1
```

## Hinweise zur DB API

- Das Projekt erwartet XML-Antworten aus dem Timetables-Endpunkt.
- Bei API-Aenderungen (Schema/Felder) muss `db_monitor/parser.py` angepasst werden.

## Monatlicher Neurologie-Report (Stroke/Notfall)

Zusätzlich ist ein MVP fuer einen klinischen Monatsreport enthalten. Der Report sucht Studien in PubMed, filtert auf klinische Relevanz und erzeugt ein PDF (5-10 Seiten je nach Anzahl Top-Studien).

### Installation

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

### Manuell ausführen

```bash
python run_neuro_report.py --email "deine.email@example.com" --top-n 10
```

Offline-Preview ohne API (Demo-PDF):

```bash
python run_neuro_report.py --demo --top-n 10
```

Optional mit fixem Zeitraum:

```bash
python run_neuro_report.py \
  --email "deine.email@example.com" \
  --start-date 2026-01-01 \
  --end-date 2026-01-31 \
  --top-n 10
```

Ausgaben:
- `data/neuro_reports/*.pdf` (Report)
- `data/neuro_reports/*.json` (strukturierte Studienmetadaten)

Der PDF-Report ist im Newsletter-Stil aufgebaut:
- Editorial Snapshot + Top-Highlights
- Methodik/Selektionslogik
- Pro Studie: Kernbotschaft, Studiendesign, Population, Endpunkt, Hauptergebnis, Statistik, klinische Einordnung, Praxisimplikation, Limitationen

### Monatliche Automation (Cron-Beispiel)

```cron
30 7 1 * * cd "/Users/konradwhittaker/Documents/New project" && "/Users/konradwhittaker/Documents/New project/.venv/bin/python" run_neuro_report.py --email "deine.email@example.com" >> "/Users/konradwhittaker/Documents/New project/cron.log" 2>&1
```

### Was klinisch priorisiert wird

- Nur Stroke- und neuro-notfallmedizinischer Fokus
- Bevorzugt RCT, Metaanalyse, hochwertige Kohorten/Register
- Ausschluss von Labor-/Tierstudien und klinisch wenig generalisierbaren Nischenthemen
- Score nach Design, Endpunkten, Statistik-Robustheit, Generalisierbarkeit und Leitlinienpotenzial
