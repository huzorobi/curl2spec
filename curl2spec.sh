#!/usr/bin/env bash
# curl2spec launcher — one click: ensure deps, start the local server, open the browser.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PORT="${CURL2SPEC_PORT:-8099}"
URL="http://127.0.0.1:${PORT}"

if [ "${1:-}" = "--stop" ]; then
  pids=$(ss -ltnp 2>/dev/null | awk -v p=":${PORT}" '$0 ~ p' | grep -o "pid=[0-9]*" | cut -d= -f2 | sort -u)
  [ -n "$pids" ] && kill $pids && echo "stopped curl2spec ($pids)" || echo "curl2spec not running on ${PORT}"
  exit 0
fi

# first run: create the venv + install Playwright (Pro mode). Manual mode needs none of this.
if [ ! -x .venv/bin/python ]; then
  echo "→ first run: setting up curl2spec (venv + Playwright)…"
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  ./.venv/bin/playwright install chromium
fi

# start the server only if the port is free
if ! (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
  echo "→ starting curl2spec on ${URL}"
  setsid ./.venv/bin/python server.py >"$HOME/.curl2spec-server.log" 2>&1 </dev/null &
  for _ in $(seq 1 20); do
    (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null && break; sleep 0.5
  done
else
  echo "→ curl2spec already running on ${URL}"
fi

# open the browser (best-effort across desktops)
( xdg-open "$URL" || sensible-browser "$URL" || firefox "$URL" || chromium "$URL" ) >/dev/null 2>&1 &
echo "→ opened ${URL}  (Manual + Pro modes). Server log: ~/.curl2spec-server.log"
echo "   Close this window any time — the server keeps running. Stop it with:  kill $(ss -ltnp | grep :'"${PORT}"' | grep -o pid=[0-9]*)"
# keep the window on the live log so the operator can see captures/errors
tail -n +1 -f "$HOME/.curl2spec-server.log" 2>/dev/null || true
