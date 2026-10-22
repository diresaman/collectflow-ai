#!/bin/sh
# One-time move of the SQLite database into PostgreSQL (M08.1).
#
#   scripts/cutover_to_postgres.sh [--commit] [SQLITE_FILE]
#
# Before running: stop runserver and run_worker, and pause the cron entry (crontab -e, comment out
# scheduled_automation.sh). PostgreSQL must be running and backend/.env must point at it
# (POSTGRES_*); e.g. `docker compose up -d db`.
#
# Steps:
#   1. Consistent backup of the SQLite file (SQLite online-backup API) into backups/, integrity
#      check and SHA-256. The original file is never modified.
#   2. `migrate` the PostgreSQL database (creates the schema; it must be empty of data).
#   3. Dry run: copy, verify every row, roll back. Report in backups/.
#   4. With --commit only: the real transfer from the same backup, committed in one transaction.
#
# Exit codes: 0 success, 1 failure (nothing committed unless step 4 printed "Transferred").

set -eu

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$BACKEND_DIR/.venv/bin/python"
COMMIT=0
SOURCE="$BACKEND_DIR/db.sqlite3"
for arg in "$@"; do
  case "$arg" in
    --commit) COMMIT=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *) SOURCE="$arg" ;;
  esac
done

cd "$BACKEND_DIR"
STAMP="$(date +%Y%m%dT%H%M%S)"
BACKUP_DIR="$BACKEND_DIR/backups"
BACKUP="$BACKUP_DIR/db-pre-postgres-$STAMP.sqlite3"
mkdir -p "$BACKUP_DIR"

[ -f "$SOURCE" ] || { echo "SQLite file not found: $SOURCE" >&2; exit 1; }

echo "== 1. Backing up $SOURCE"
"$PYTHON" - "$SOURCE" "$BACKUP" <<'PY'
import sqlite3, sys
source, target = sys.argv[1], sys.argv[2]
src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
dst = sqlite3.connect(target)
src.backup(dst)  # consistent snapshot, including committed WAL content
dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file: no -wal/-shm side files
dst.close(); src.close()
check = sqlite3.connect(f"file:{target}?mode=ro", uri=True).execute("PRAGMA integrity_check").fetchone()[0]
if check != "ok":
    sys.exit(f"Backup failed integrity_check: {check}")
PY
chmod 600 "$BACKUP"
sha256sum "$BACKUP" > "$BACKUP.sha256"
echo "   backup: $BACKUP"
echo "   sha256: $(cut -d' ' -f1 "$BACKUP.sha256")"

echo "== 2. Migrating PostgreSQL"
"$PYTHON" -W ignore manage.py migrate --noinput

echo "== 3. Dry run (copy, verify, roll back)"
SQLITE_SOURCE_PATH="$BACKUP" "$PYTHON" -W ignore manage.py migrate_from_sqlite --dry-run \
  --report "$BACKUP_DIR/transfer-dryrun-$STAMP.md"

if [ "$COMMIT" -ne 1 ]; then
  echo "Dry run passed. Review $BACKUP_DIR/transfer-dryrun-$STAMP.md, then re-run with --commit."
  exit 0
fi

echo "== 4. Transfer (one transaction)"
SQLITE_SOURCE_PATH="$BACKUP" "$PYTHON" -W ignore manage.py migrate_from_sqlite \
  --report "$BACKUP_DIR/transfer-report-$STAMP.md"
echo "Done. Keep $BACKUP (and its .sha256) for rollback; the original $SOURCE is unchanged."
