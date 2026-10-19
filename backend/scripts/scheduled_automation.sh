#!/bin/sh
# Cron entry point for unattended proactive automation.
#
# Runs the existing `manage.py run_automation` (trigger SCHEDULED): sync from Google Sheets,
# apply the cooldown/eligibility policy, draft reminders with Gemini. It never sends email;
# drafts wait for human approval. Overlapping runs are refused by the automation lease
# (exit code 2). Output is appended to logs/automation.log with start/end timestamps.
#
# Environment comes from backend/.env; variables set by the caller (e.g. SQLITE_PATH) win.

set -u

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="${COLLECTFLOW_AUTOMATION_LOG:-$BACKEND_DIR/logs/automation.log}"

mkdir -p "$(dirname "$LOG_FILE")"
cd "$BACKEND_DIR" || exit 1

{
  echo "=== $(date -Iseconds) scheduled run_automation started (pid $$)"
  "$BACKEND_DIR/.venv/bin/python" -W ignore::FutureWarning manage.py run_automation
  status=$?
  echo "=== $(date -Iseconds) finished, exit=$status"
} >> "$LOG_FILE" 2>&1

exit "$status"
