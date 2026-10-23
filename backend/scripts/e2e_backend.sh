#!/usr/bin/env bash
# Browser tests only (Playwright starts this; see frontend/playwright.config.ts).
# Recreates the throwaway *_e2e database, seeds it, then runs the API and a worker with fake Gemini and Gmail.
# Providers are switched off by environment; the e2e commands refuse any database not ending in _e2e.
set -euo pipefail
cd "$(dirname "$0")/.."

export POSTGRES_DB="${E2E_DB:-collectflow_e2e}"
export DJANGO_DEBUG=true SECURE_COOKIES=false
export GEMINI_API_KEY=e2e-fake GEMINI_MODEL=e2e-fake
export GOOGLE_SHEETS_SPREADSHEET_ID="" GMAIL_READ_ENABLED=false
export GMAIL_SENDER_EMAIL=ar@e2e.test GMAIL_ALLOWED_RECIPIENTS=@e2e.test
export GMAIL_TOKEN_FILE=/nonexistent/token.json GMAIL_OAUTH_CLIENT_FILE=/nonexistent/client.json
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python
PORT="${E2E_API_PORT:-8002}"

case "$POSTGRES_DB" in *_e2e) ;; *) echo "Refusing: E2E_DB must end with _e2e" >&2; exit 1 ;; esac

"$PYTHON" - <<'EOF'
import os

import django
import psycopg

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()
from django.conf import settings

db = settings.DATABASES["default"]
name = db["NAME"]
assert name.endswith("_e2e"), name
with psycopg.connect(dbname="postgres", user=db["USER"], password=db["PASSWORD"], host=db["HOST"], port=db["PORT"],
                     autocommit=True) as conn:
    conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{name}"')
print(f"recreated {name}")
EOF

"$PYTHON" manage.py e2e_setup
"$PYTHON" manage.py e2e_worker &
WORKER=$!
trap 'kill $WORKER 2>/dev/null || true' EXIT
"$PYTHON" manage.py e2e_server "127.0.0.1:$PORT"
