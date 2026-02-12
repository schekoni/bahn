# Kostenloses 24/7 Setup (GitHub Actions + Streamlit Cloud)

## Ziel

- Daten stündlich automatisch sammeln (ohne eigenen laufenden Rechner)
- Dashboard jederzeit online in Streamlit Cloud

## Voraussetzungen

- GitHub Account
- Streamlit Community Cloud Account
- DB API Zugang (`DB_CLIENT_ID`, `DB_API_KEY`)

## 1. Repo nach GitHub pushen

```bash
git add .
git commit -m "setup free cloud automation"
git push
```

## 2. GitHub Secrets setzen

In GitHub Repo:
- `Settings -> Secrets and variables -> Actions -> New repository secret`

Anlegen:
- `DB_CLIENT_ID`
- `DB_API_KEY`

## 3. Workflow starten und prüfen

Workflow-Datei:
- `.github/workflows/db-collector.yml`
- Zeitplan: stündlich **nur tagsüber** (UTC 05:00-20:00)
  - Winterzeit (CET): 06:00-21:00
  - Sommerzeit (CEST): 07:00-22:00

Manueller Start:
- `Actions -> DB Collector -> Run workflow`

Erwartung:
- Workflow laeuft erfolgreich durch.
- Falls neue Daten kamen, gibt es einen Commit `chore: update punctuality data`.

## 4. Streamlit Community Cloud deployen

- In Streamlit Cloud: `New app`
- Repo waehlen
- Branch: `main`
- Main file path: `dashboard.py`
- Deploy

## 5. Betrieb

- GitHub Actions aktualisiert die DB stündlich.
- Streamlit App zeigt die Daten aus dem Repo.

## Konkrete Schritt-für-Schritt-Anleitung

1. Im Projektordner committen und zu GitHub pushen:

```bash
cd "/Users/konradwhittaker/Documents/New project"
git add .github/workflows/db-collector.yml FREE_CLOUD_SETUP.md README.md dashboard.py
git commit -m "chore: daytime cloud collector setup"
git push
```

2. In GitHub Repo `Settings -> Secrets and variables -> Actions`:
- `New repository secret` -> `DB_CLIENT_ID` eintragen
- `New repository secret` -> `DB_API_KEY` eintragen

3. In GitHub `Actions`:
- Workflow `DB Collector` öffnen
- `Run workflow` klicken
- Warten bis Status `Success`

4. Prüfen, ob Daten-Commit erzeugt wurde:
- In `Code -> Commits` nach `chore: update punctuality data` schauen

5. Streamlit Cloud öffnen:
- Neue App erstellen
- Dein GitHub-Repo auswählen
- Branch `main`
- Main file path `dashboard.py`
- Deploy

6. Nach Deploy kontrollieren:
- Dashboard öffnet
- Neue Daten erscheinen nach jedem erfolgreichen Workflow-Lauf tagsüber

## Troubleshooting

- Keine Updates sichtbar:
  - Prüfen, ob Workflow erfolgreich ist.
  - Prüfen, ob der letzte Workflow einen DB-Commit erzeugt hat.
- Auth-Fehler im Workflow (`401`):
  - Secrets prüfen (`DB_CLIENT_ID`, `DB_API_KEY`).
  - DB API Subscription prüfen.
