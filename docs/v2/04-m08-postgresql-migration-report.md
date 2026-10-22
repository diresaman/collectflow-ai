# 04 — M08.1: PostgreSQL migration report

- **Date:** 2026-10-22
- **Scope:** move the application database from SQLite to PostgreSQL 16, add Docker Compose, and
  transfer the existing data. No domain changes; no Phase 02 work.
- **Status:** **complete.** Implemented, tested on PostgreSQL 16 and rehearsed. The owner approved
  the cutover, which was run and verified on 2026-10-22 (section 6.3).

## 1. Migration assessment: SQLite-specific assumptions found

| Area | Finding | Resolution |
| --- | --- | --- |
| Settings | WAL `init_command`, `transaction_mode: IMMEDIATE`, busy timeout, a SQLite test-file path | Replaced by the PostgreSQL backend configured from `POSTGRES_*`, with `CONN_MAX_AGE` and health checks |
| Timeline triggers | Migration `timeline/0001` ran SQLite-only SQL (`RAISE(ABORT)`), so a fresh PostgreSQL `migrate` would fail | The operation became vendor-aware (`RunPython`). SQLite gets the identical statements; PostgreSQL gets a PL/pgSQL trigger. Databases that already applied 0001 are unaffected, because applied migrations never re-run. This is the only edit to a committed migration, and it is needed for PostgreSQL to migrate at all |
| Row locks | `select_for_update()` in approve, edit, generation, sync and reconciliation was a no-op on SQLite | All calls already sit inside `atomic()`; on PostgreSQL they now really lock. No change needed |
| Conditional UPDATEs | Send reservation, lease, job finish and workflow transitions | Correct on PostgreSQL: under READ COMMITTED a blocked UPDATE re-checks its WHERE clause after the other transaction commits. Kept |
| Job claiming | Conditional UPDATE loop | Now `SELECT … FOR UPDATE SKIP LOCKED` plus the conditional UPDATE (the design's mechanism) |
| Test flushes | `TransactionTestCase` uses TRUNCATE on PostgreSQL | The test helper lifts the timeline triggers on every database it flushes |
| Partial unique and check constraints | Supported by both | Verified on PostgreSQL by the existing tests (one active follow-up, one SENDING attempt, idempotency keys, amount and status checks, lowercased client email) |
| JSON lookups (`payload__…`) | Supported by both (jsonb on PostgreSQL) | Covered by existing tests |
| Date/time | `USE_TZ=True`; SQLite stored UTC text | `timestamptz` in UTC; `DJANGO_TIME_ZONE` still decides the business date |
| Docs and scripts | `SQLITE_PATH` in the cron wrapper, scheduling guide, README | Updated to PostgreSQL |

## 2. Architecture changes

- **Database:** PostgreSQL 16 (`postgres:16-alpine`) is the only runtime database; the driver is psycopg 3
  (`psycopg[binary]`). SQLite is only read by the one-time transfer command, through an optional
  `sqlite_source` alias opened with `mode=ro` and excluded from migrations by `config/db_routers.py`.
- **Docker Compose** (`docker-compose.yml`):
  - `db`: persistent `pgdata` volume, `pg_isready` health check.
  - `api`: runs `migrate`, then `runserver 0.0.0.0:8000`; HTTP health check.
  - `worker`: `run_worker`, stops with SIGTERM.
  - `web`: production Next.js build.
  - Configuration comes from `backend/.env` (path overridable with `COLLECTFLOW_ENV_FILE`). All ports are
    published on 127.0.0.1 only (`WEB_HOST_PORT`, `API_HOST_PORT`, `POSTGRES_HOST_PORT`, default 5433).
  - No Redis.
- **Job claiming:** `SELECT … FOR UPDATE SKIP LOCKED`, so several workers can run safely
  (`--scale worker=N`).
- **Timeline:** a PL/pgSQL trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` with SQLSTATE 23001
  (raised as `IntegrityError`).
- **Proxy fix found by the Compose smoke test:** the same-origin check compared the browser's Origin
  with Next's own URL, which inside a container is `0.0.0.0:3000`, so every write was refused. It now
  compares Origin with the `Host` header the browser sent. Cross-site writes are still refused (tested),
  and Django's CSRF token check still applies.
- **Unchanged:** API contracts, business rules, the frontend apart from that check, and Gmail
  at-most-once delivery (no retries; unknown outcomes need reconciliation).

## 3. Data transfer tooling

- **`manage.py migrate_from_sqlite [--dry-run] [--report PATH]`** (`apps/core/sqlite_transfer.py`)
  runs as **one PostgreSQL transaction**:
  1. **Preflight:**
     - the source is SQLite, passes `PRAGMA integrity_check`, and has exactly the same applied
       migrations as the target;
     - the snapshot shows no worker alive in its last 2 minutes and no held automation lease;
     - the target is PostgreSQL, migrated and empty.
  2. **Copy:** every row of `auth` (users, groups), `accounts`, `invoices`, `followups`, `automation` and
     `timeline`, with original primary keys, as raw saves (no model logic or signals).
     - ContentTypes and Permissions are recreated by `migrate` and referenced by natural key.
     - Sessions and admin log entries are not copied, so users sign in again.
  3. **Verify:**
     - every copied model compared row by row, field by field (including primary keys, foreign keys,
       JSON and microsecond timestamps);
     - deferred FK constraints forced with `SET CONSTRAINTS ALL IMMEDIATE`;
     - id sequences past the copied maximum;
     - status totals and a SHA-256 digest of the timeline;
     - JSON and relationship spot checks.
     - Any difference aborts the transaction.
  4. **Cutover policy** (section 4).
  5. **Commit,** or roll back for `--dry-run`. A Markdown report is written either way.
- **`backend/scripts/cutover_to_postgres.sh [--commit] [SQLITE_FILE]`:**
  - a consistent backup through SQLite's online-backup API into `backend/backups/` (self-contained file,
    `PRAGMA integrity_check`, SHA-256, mode 600; the source is never modified);
  - `migrate`;
  - a dry run;
  - and, only with `--commit`, the transfer from the same backup.

**A defect caught by the verification step:** the first version used Django's JSON fixtures
(`dumpdata`/`loaddata`). Their JSON encoder truncates timestamps to milliseconds
(`10:16:29.945683` became `10:16:29.945000`), and row-by-row verification flagged every row with a
timestamp. The copy now uses Django's in-memory Python serializer, which keeps full precision.
A plain dumpdata/loaddata migration would have changed every stored timestamp without any error.

## 4. Cutover policy for unfinished work

| State in the snapshot | Action on the PostgreSQL copy | Why |
| --- | --- | --- |
| `PENDING` delivery job | Attempt → `FAILED` (`cutover_not_sent`), reminder → `APPROVED`, job → `FAILED` (`cutover_cancelled`), `SEND_CANCELLED` timeline event | Never claimed, so nothing reached Gmail. A person confirms the send again; nothing is sent automatically |
| `RUNNING` delivery job | Job → `FAILED` (`abandoned`); attempt and reminder stay `SENDING` | Gmail may have been called. Left for `reconcile_delivery`; never retried |
| `SENDING` / `DELIVERY_UNKNOWN` reminders | Copied unchanged | Outcome uncertain; reconcile after checking Gmail |
| Other `PENDING`/`RUNNING` jobs | `FAILED` (`cutover_cancelled`); a sync run they were running → `FAILED` | No provider call is re-run without a person asking |
| `RUNNING` automation runs | `FAILED` ("Abandoned…"); running steps `FAILED`, pending steps `SKIPPED` | Same as the existing abandoned-run recovery |
| Held automation lease | Released | No process from the old database survives |

The source SQLite file keeps its original state. The owner's current database has none of these
states (0 jobs, 0 unresolved deliveries, lease free, no running runs), so the policy will not change
anything there. It is covered by tests with a synthetic snapshot.

## 5. Tests

All on PostgreSQL 16.15, with providers mocked.

| Check | Result |
| --- | --- |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| Fresh `migrate` on an empty PostgreSQL 16 database | All migrations apply; timeline triggers present (`timeline_event_no_update_delete`, `timeline_event_no_truncate`) |
| Backend suite | **269 tests, OK** on the Compose PostgreSQL (272 s on a heavily loaded machine; 256 existing + 13 new) |
| Frontend `lint`, `typecheck`, `build` | All exit 0 |

New or adapted tests in M08.1:
- **Transfer** (`apps/core/tests_sqlite_transfer.py`, against a real SQLite snapshot file):
  - full transfer with users, password hashes, microsecond timestamps, nested JSON, timeline events,
    queued, running and unknown deliveries, a running sync and automation run, and an expired lease;
  - every cutover action;
  - the source left unchanged;
  - sequences continue after the copied ids;
  - a dry run rolls back;
  - refusals: non-empty target, live worker, live lease, schema mismatch;
  - a forced verification failure commits nothing.
- **PostgreSQL concurrency:**
  - a claim skips a row locked by another connection without waiting (SKIP LOCKED);
  - 4 workers claim 20 jobs exactly once each;
  - 4 parallel approvals have exactly one winner;
  - parallel send requests plus parallel workers call Gmail at most once;
  - a failed enqueue rolls back the send reservation.
- **Timeline:** UPDATE, DELETE and TRUNCATE are rejected by the database.
- **Kept and passing on PostgreSQL:**
  - authentication and CSRF flows (real CSRF client);
  - route-wide 401/403;
  - worker recovery of stale jobs;
  - idempotency keys;
  - the backfill migration test.

## 6. Verification results

### 6.1 Rehearsal on a copy of the owner's database (Docker Compose, fresh volume)

The full procedure ran against the Compose PostgreSQL:
- `cutover_to_postgres.sh --commit`: 137 rows in 16 models transferred and verified.
- The stack (api, worker, web) started on the transferred data.
- Sign-in through the proxy showed 7 invoices (2 clients, 1 PAID, 6 UNPAID), 6 follow-ups (5 SENT,
  1 REJECTED), delivery attempts with their Gmail IDs, 27 runs, and the last workflow (FINALIZED).
  The next run id was 28 (the sequence continues).
- A second transfer was refused ("target already contains data").
- A synthetic sync job was queued through the UI proxy, claimed by the worker container and completed.
  It made no provider call: "Google Sheets is not configured".
- No Gmail, Gemini or Sheets call was made at any point.

### 6.2 The owner's database: backup and dry run (nothing committed)

| Item | Value |
| --- | --- |
| Source | `backend/db.sqlite3` (unchanged: SHA-256 identical before and after) |
| Backup | `backend/backups/db-pre-postgres-20261022T141831.sqlite3`, integrity `ok`, SHA-256 `f19d8974469c4806d3ef27272f781b361d70aa2e07decc2df68c6b49781233ee` |
| Dry run | 137 rows in 16 models, every row identical; report `backend/backups/transfer-dryrun-20261022T141831.md` |

| Model | Rows | | Model | Rows |
| --- | ---: | --- | --- | ---: |
| invoices.invoice | 7 | | followups.followup | 6 |
| invoices.client | 2 | | followups.deliveryattempt | 5 |
| invoices.automationrun | 27 | | followups.generationattempt | 7 |
| automation.automationrunitem | 62 | | automation.workflowstep | 20 |
| automation.automationlease | 1 | | timeline, users, profiles, jobs | 0 |

Status totals matched:
- invoices: PAID 1, UNPAID 6; all in the sheet;
- follow-ups: SENT 5, REJECTED 1;
- delivery attempts: SENT 5;
- runs: COMPLETED 10, SUCCEEDED 15, FAILED 2.

Sequences were set past the maximum ids (runs 27, items 62, steps 20, generation attempts 7). JSON
checks: all 6 follow-up snapshots name their own invoice, all 7 invoices match their client's email,
and the 4 sync row-error entries were preserved. Not copied: 80 permissions and 20 content types
(recreated by `migrate`); 0 sessions; 0 admin log entries.

### 6.3 Owner's cutover (2026-10-22, approved by the owner)

| Step | Result |
| --- | --- |
| Stop writers | The cron entry was commented out (crontab saved to `backend/backups/crontab-before-cutover.txt`); the dev `runserver` and `next dev` were stopped |
| Configuration | `backend/.env` backed up to `backend/backups/env-before-cutover`; `POSTGRES_*` appended with a generated password |
| Database | `docker compose up -d db`: PostgreSQL 16.15, volume `collectflow_pgdata` |
| Backup | `backend/backups/db-pre-postgres-20261022T143057.sqlite3`, integrity `ok`, SHA-256 `f19d8974…233ee` (identical to the morning backup: no writes in between) |
| Transfer | `cutover_to_postgres.sh --commit`: dry run, then commit; **137 rows in 16 models transferred and verified**; report `backend/backups/transfer-report-20261022T143057.md`; no cutover actions (nothing unfinished) |
| Source | `backend/db.sqlite3` SHA-256 identical before and after |
| Stack | `docker compose up -d --build`: db healthy, api healthy (migrate was a no-op), worker and web running |
| Admin account | `collectflow` (ADMIN), created with a generated password stored in `backend/credentials/admin-initial-password.txt` (mode 600, gitignored) |
| Verification through the UI proxy | Signed in as `collectflow`. Dashboard: 7 invoices, 6 unpaid, 5 overdue. 2 clients, all invoices in the sheet. Follow-ups: 5 SENT, 1 REJECTED. Latest sync #27 SUCCEEDED at `2026-10-22T05:00:14.846832Z` (same value, to the microsecond, as SQLite). Workflow #26 FINALIZED. Next run id 28 |
| Worker | A `core.ping` job (no-op) was queued, claimed by the worker container and finished `SUCCEEDED {"pong": true}`; `worker_alive` true |
| No provider action | The only job in the database is that ping. No Sheets, Gemini or Gmail call was made during the cutover |
| Cron | `run_automation --dry-run` in cron's minimal environment reached PostgreSQL (7 invoices evaluated, nothing changed); the crontab was restored byte for byte |

The Compose stack now serves the app: http://localhost:3000 (UI) and http://localhost:8000 (API).
Development with `npm run dev` and `runserver` on the host still works against the same database,
after `docker compose stop web api worker`.

## 7. Known limitations

- Django runs with `runserver` inside the `api` container. That is fine for this local stack; production
  needs gunicorn or uvicorn behind HTTPS.
- The application's database role owns its tables, so it could disable the timeline trigger. A
  separate least-privilege role is future work.
- `authorize_gmail` opens a browser, so it runs on the host. The token is shared with the containers
  through the mounted `backend/credentials/`.
- The daily cron still runs `run_automation` on the host. It needs the Compose `db` running.
- The test suite takes about 3–4 minutes on PostgreSQL (TRUNCATE-based flushes for the concurrency and
  transfer tests).
- Sessions are not transferred; existing users sign in again (the current database has no users).

## 8. Rollback

- **Before the PostgreSQL database accepts new writes:** nothing to undo on the SQLite side, because the
  original `db.sqlite3` and the verified backup are never modified. Check out the previous commit
  (`676b43c`), which runs on SQLite, and restart. Optionally `docker compose down -v` to discard the
  PostgreSQL volume.
- **After PostgreSQL has accepted new writes:** do not switch back to the old SQLite snapshot; that
  would lose those writes.
  1. Stop writes: `docker compose stop api worker web` and pause cron.
  2. `pg_dump -Fc` the PostgreSQL database.
  3. Identify the writes since the cutover. The timeline lists them per invoice. Also check
     `AutomationRun`, `FollowUp` and `DeliveryAttempt` rows created after the cutover time.
  4. Fix forward on PostgreSQL where possible. If a return to SQLite is unavoidable, re-create those
     records deliberately (for example re-sync the sheet). Never replay sends; reconcile any delivery
     whose outcome is unknown.

## 9. Cutover runbook (needs the owner's approval)

1. Pause cron: `crontab -e`, comment out `scheduled_automation.sh`. Stop any `runserver`,
   `run_worker` and `npm run dev`.
2. Add `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (generated), `POSTGRES_HOST=localhost` and
   `POSTGRES_PORT=5433` to `backend/.env`.
3. `docker compose up -d db`.
4. `backend/scripts/cutover_to_postgres.sh`, which makes a fresh backup and a dry run. Review the
   report, then `backend/scripts/cutover_to_postgres.sh --commit`.
5. `docker compose up -d --build`, then `docker compose exec api python manage.py create_user <you> --role ADMIN`.
6. Verify: sign-in, dashboard counts and history as in section 6.2, one synthetic job, and no provider
   calls in the worker log.
7. Re-enable cron (it now runs against PostgreSQL).
