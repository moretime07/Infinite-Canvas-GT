#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Infinite-Canvas-GT"
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi
echo "Open http://127.0.0.1:3000/ in your browser. Ctrl+C stops the service."
exec .venv/bin/python main.py
