#!/usr/bin/env bash
# curl2spec Pro: one-command launcher for the auto-capture server.
# Free manual mode needs none of this: just open index.html in a browser.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "→ first run: creating .venv and installing Playwright…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  ./.venv/bin/playwright install chromium
fi

echo "→ starting curl2spec Pro on http://127.0.0.1:${CURL2SPEC_PORT:-8099}"
exec ./.venv/bin/python server.py
