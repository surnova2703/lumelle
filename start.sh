#!/usr/bin/env bash
# Lumo AI for macOS and Linux:  ./start.sh   (or: bash start.sh)
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating the Python environment..."
  python3 -m venv .venv || { echo "Python 3.9+ is required (on Ubuntu: sudo apt install python3-venv)"; exit 1; }
fi

# Libraries are installed once (internet needed only the first time).
if [ ! -f .venv/.deps-ok ]; then
  echo "Installing libraries..."
  .venv/bin/python -m pip install --default-timeout=120 --retries 10 -r requirements.txt
  touch .venv/.deps-ok
fi

exec .venv/bin/python run.py "$@"
