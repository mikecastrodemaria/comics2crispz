#!/usr/bin/env bash
# comics2crispz - one-click start (Linux/macOS): serves the most recently
# edited book under books/ (or the folder you pass) and opens the browser.
#   ./start.sh                     # latest book
#   ./start.sh books/plein-ecran   # a specific book
#   ./start.sh --port 8771         # second book side by side
set -e
cd "$(dirname "$0")"
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "[start] no .venv yet - creating it..."
    python3 -m venv .venv
    "$PY" -m pip install --quiet --upgrade pip
    "$PY" -m pip install --quiet -r requirements.txt
    [ -f config.json ] || cp config-sample.json config.json
    [ -f books/exemple/project.json ] || "$PY" tools/make_example.py
fi
# requirements.txt changed since the last install (new optional deps such as
# rembg)? bring the venv up to date before serving.
if ! cmp -s requirements.txt .venv/requirements.stamp; then
    echo "[start] requirements.txt changed - updating the venv..."
    "$PY" -m pip install --quiet -r requirements.txt
    cp requirements.txt .venv/requirements.stamp
fi
exec "$PY" c2c_server.py --open "$@"
