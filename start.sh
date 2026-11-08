#!/usr/bin/env bash
# Lumelle AI for macOS and Linux:  ./start.sh   (or: bash start.sh)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating the Python environment..."
  python3 -m venv .venv || { echo "Python 3.9+ is required (on Ubuntu: sudo apt install python3-venv)"; exit 1; }
fi

# Libraries are installed once (internet needed only the first time).
# …and again whenever requirements.txt changes (a new version added a library).
if ! cat requirements.txt requirements-voice.txt | cmp -s - .venv/.deps-ok; then
  echo "Installing libraries..."
  if .venv/bin/python -m pip install --default-timeout=120 --retries 2 -r requirements.txt; then
    # voice input is optional: if it can't be installed, Lumelle still starts
    .venv/bin/python -m pip install --default-timeout=120 --retries 2 -r requirements-voice.txt \
      || echo "! Voice input (Whisper) was not installed. Everything else works; run the script again later to retry."
    cat requirements.txt requirements-voice.txt > .venv/.deps-ok
  elif .venv/bin/python -c "import fastapi, uvicorn, httpx" 2>/dev/null; then
    # no internet: start with the libraries that are already there and try again next time
    echo "! New libraries could not be installed (no internet?). Lumelle starts anyway; a new feature may wait until the next start online."
  else
    echo "Could not install the libraries. Check the internet connection and run the script again."
    exit 1
  fi
fi

exec .venv/bin/python run.py "$@"
