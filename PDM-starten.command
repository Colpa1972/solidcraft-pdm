#!/bin/bash
# Startet das PDM (Doppelklick). Daten liegen in ~/Documents/PDM
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 fehlt. Bitte die Befehlszeilenwerkzeuge installieren: xcode-select --install"
  read -p "Enter zum Beenden"; exit 1
fi
python3 pdm.py
