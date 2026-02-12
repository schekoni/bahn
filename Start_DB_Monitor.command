#!/bin/zsh
set -e

PROJECT_DIR="/Users/konradwhittaker/Documents/New project"
LOG_FILE="$PROJECT_DIR/manual_run.log"

cd "$PROJECT_DIR"

if [ ! -d ".venv" ]; then
  echo "Fehler: .venv wurde nicht gefunden in $PROJECT_DIR"
  echo "Bitte zuerst einmal Setup ausfuehren."
  read -k 1 "?Taste druecken zum Beenden..."
  echo
  exit 1
fi

source ".venv/bin/activate"

echo "[1/2] Daten werden im Hintergrund aktualisiert..."
python -u run_collection.py >> "$LOG_FILE" 2>&1 &
COLLECT_PID=$!
echo "Collector PID: $COLLECT_PID"
echo "Log: $LOG_FILE"

echo "[2/2] Dashboard wird gestartet..."
echo "Falls kein Browser aufspringt: http://127.0.0.1:8501"
streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8501
