# Scheduled automation (Linux cron)

CollectFlow AI can run its proactive automation unattended. Once a day, cron calls the
existing `python manage.py run_automation` command. It syncs invoices from Google Sheets,
evaluates every invoice against the deterministic policy, and drafts reminders with Gemini for
the eligible ones. **It never approves, never sends and never retries a delivery.** Drafts wait
in the approval queue for a person.

- [1. How it works](#1-how-it-works)
- [2. Prerequisites](#2-prerequisites)
- [3. Cron configuration](#3-cron-configuration)
- [4. Install the schedule](#4-install-the-schedule)
- [5. Inspect execution logs](#5-inspect-execution-logs)
- [6. Verify an unattended run](#6-verify-an-unattended-run)
- [7. Disable scheduling](#7-disable-scheduling)
- [8. Known limitations of local scheduling](#8-known-limitations-of-local-scheduling)
- [9. Verification record (Milestone 07)](#9-verification-record-milestone-07)

## 1. How it works

```
cron (user crontab, system time zone)
  └─▶ backend/scripts/scheduled_automation.sh        absolute paths; appends to logs/automation.log
        └─▶ backend/.venv/bin/python manage.py run_automation      trigger = SCHEDULED
              └─▶ AutomationOrchestrator.run()        same code path as the "Run Automation" button
                    ├─ AutomationLease                one run at a time (manual + scheduled); else exit 2
                    ├─ sync_invoices()                Google Sheets → SYNC run (trigger SCHEDULED)
                    ├─ policy.evaluate()              eligibility, cooldown, existing drafts, unresolved deliveries
                    ├─ FollowUpGenerationService      Gemini → FollowUp(status=DRAFT); lock + unique constraint
                    └─ AutomationRun / AutomationRunItem / WorkflowStep / GenerationAttempt   audit history
```

The wrapper script contains no business logic. It:
- changes into `backend/` and runs the project virtualenv's Python by absolute path;
- lets Django load `backend/.env` (through `config/settings.py`), so cron needs no environment
  of its own;
- appends output to the log between `=== <ISO timestamp> scheduled run_automation started` and
  `=== <ISO timestamp> finished, exit=<code>` lines, and returns the command's exit code.

| What scheduled automation does | What it never does (and why) |
| --- | --- |
| Syncs invoices from Google Sheets | **Approve drafts.** Approval is only reachable via `POST /follow-ups/{id}/approve/`, which the orchestrator doesn't call. |
| Evaluates overdue invoices | **Send Gmail.** The orchestrator has no Gmail dependency, and sending requires an `APPROVED` reminder plus an explicit `POST /send/`. Tests patch `GmailClient.send` to fail if called. |
| Generates AI drafts (`DRAFT`) | **Retry uncertain deliveries.** Invoices with a `SENDING`/`DELIVERY_UNKNOWN` reminder are skipped as `delivery_unresolved`. Only `reconcile_delivery` (manual) resolves them. |
| Records automation history | **Bypass eligibility.** It uses the same `ineligibility()` rules and cooldown policy as manual generation. Generation re-checks them under a lock. |

**Overlap protection** reuses the existing database lease (`AutomationLease`). A second
invocation while a run is active (scheduled or manual) does nothing and exits with code `2`
("Another automation run is in progress"). A crashed run's lease expires after
`AUTOMATION_LEASE_SECONDS` (default 600s). No extra lock file (`flock`) is needed.

**Exit codes** (written to the log): `0` completed or partial · `1` failed (sync failed, or every
draft failed) · `2` another run held the lease.

## 2. Prerequisites

1. Backend installed with its virtualenv at `backend/.venv`, and `python manage.py migrate` run.
2. `backend/.env` configured: `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SHEETS_RANGE`,
   `GOOGLE_APPLICATION_CREDENTIALS`, `GEMINI_API_KEY`, `GEMINI_MODEL`. Gmail settings are not
   needed by scheduled runs.
3. A working cron daemon: `systemctl is-active cron` → `active`.
4. The wrapper is executable: `chmod +x backend/scripts/scheduled_automation.sh`.
5. A manual test in cron's minimal environment succeeds:

   ```bash
   env -i HOME="$HOME" /bin/sh -c '/home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh'; echo "exit=$?"
   tail -n 15 /home/dires/projects/collectflow-ai/backend/logs/automation.log
   ```

   For a preview with no changes, Gemini calls or Gmail calls, run
   `cd backend && .venv/bin/python manage.py run_automation --dry-run`.

The Django and Next.js servers do **not** need to be running for scheduled runs. The machine
must be on and awake at the scheduled time.

## 3. Cron configuration

**Installed on this machine** (user `dires`, verified 2026-10-19):

```cron
# collectflow-ai: daily proactive automation (drafts only, never sends email)
0 8 * * * /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh
```

Example daily schedule: every day at 08:00 in the system time zone (Africa/Addis_Ababa, EAT).
That is 05:00 UTC, the same calendar date as `DJANGO_TIME_ZONE=UTC`. Other examples:

```cron
30 11 * * *   …/scheduled_automation.sh     # daily 11:30 (after the Gemini free-tier reset, see §8)
0 8 * * 1-5   …/scheduled_automation.sh     # weekdays only
```

Optional overrides go before the path (variables set by cron win over `backend/.env`):

```cron
0 8 * * * COLLECTFLOW_AUTOMATION_LOG=/var/tmp/collectflow.log /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh
```

## 4. Install the schedule

```bash
chmod +x /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh
mkdir -p /home/dires/projects/collectflow-ai/backend/logs
( crontab -l 2>/dev/null | grep -v -e 'collectflow-ai' -e 'scheduled_automation.sh'; \
  echo '# collectflow-ai: daily proactive automation (drafts only, never sends email)'; \
  echo '0 8 * * * /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh' \
) | crontab -
crontab -l | grep -A1 collectflow-ai
```

The `grep -v` makes this idempotent: re-running it replaces the CollectFlow entry instead of
adding a second one. Other crontab lines are kept.

To install from a saved file, use `crontab - < file` rather than `crontab file`. Debian's
`crontab` rejects very long file paths.

## 5. Inspect execution logs

```bash
# CollectFlow's own log: one block per run, with timestamps and exit code
tail -n 40 /home/dires/projects/collectflow-ai/backend/logs/automation.log
grep -E '^=== .*(started|exit=)' /home/dires/projects/collectflow-ai/backend/logs/automation.log | tail

# Did cron start it? (cron's own record)
grep 'CRON\[' /var/log/syslog | grep scheduled_automation | tail
journalctl -t CRON --since today | grep scheduled_automation      # alternative
```

A successful block looks like this:

```
=== 2026-10-19T21:16:01+03:00 scheduled run_automation started (pid 19160)
Sync SUCCEEDED: processed=7 created=7 updated=0 unchanged=0 rejected=0
Reasons: ai_unavailable=3, not_overdue=1, not_unpaid=1
Automation run 1 PARTIAL: evaluated=7 drafts_generated=2 skipped=2 failed=3
Drafts generated — awaiting approval. No email was sent.
=== 2026-10-19T21:17:40+03:00 finished, exit=0
```

The log holds invoice IDs, counts and reason codes only. It never contains customer emails,
draft text or credentials. It grows by a few lines per day. Rotate it with `logrotate`, or
truncate it with `: > backend/logs/automation.log` when needed.

In the app, scheduled runs appear in the dashboard's **Automation** panel and in `/automation`
(labelled "scheduled run"). New drafts appear in the dashboard **Approval queue**.

## 6. Verify an unattended run

Use fictional invoices, and a test database if you don't want to touch your working data.

1. **Test database (optional):** create a separate PostgreSQL database and migrate it:
   `docker compose exec db createdb -U collectflow cf_test && cd backend && POSTGRES_DB=cf_test .venv/bin/python manage.py migrate`
2. **Short interval:** add a temporary, clearly tagged line next to the daily one:

   ```bash
   crontab -l > /tmp/crontab.before
   ( cat /tmp/crontab.before; \
     echo '# collectflow-test (temporary)'; \
     echo '*/2 * * * * POSTGRES_DB=cf_test COLLECTFLOW_AUTOMATION_LOG=/tmp/cf-test.log /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh' \
   ) | crontab -
   ```

3. **Wait** for two or three firings without touching the app, then check:
   - `grep 'CRON\[' /var/log/syslog | grep scheduled_automation`: cron started the command.
   - `/tmp/cf-test.log`: `Sync SUCCEEDED`, the reasons, and `drafts_generated=N`.
   - Repeated runs report `draft_awaiting_review` for invoices that already have a draft, so
     there are no duplicate active drafts.
   - Database: run `POSTGRES_DB=cf_test .venv/bin/python manage.py shell`, then
     check that `AutomationRun.objects.filter(kind="AUTOMATION")` all have
     `trigger == "SCHEDULED"`, that every `FollowUp` is `DRAFT`, and that
     `DeliveryAttempt.objects.count() == 0`.
   - Approval queue: run `runserver` with the same `POSTGRES_DB` and open the dashboard, or
     `GET /api/follow-ups/` and look for `status == "DRAFT"`.
4. **Restore** the daily schedule: `crontab - < /tmp/crontab.before && crontab -l`, then drop the
   test database: `docker compose exec db dropdb -U collectflow cf_test`.

## 7. Disable scheduling

```bash
crontab -l | grep -v -e 'collectflow-ai' -e 'scheduled_automation.sh' | crontab -
crontab -l | grep -c scheduled_automation     # → 0
```

To pause instead, comment the line out with `crontab -e`. Disabling doesn't affect the
**Run Automation** button, `run_automation` run by hand, or drafts that already exist.

## 8. Known limitations of local scheduling

- **The machine must be on.** Cron doesn't catch up: a run missed while the laptop is off or
  asleep is skipped, and the next run drafts whatever is eligible then.
- **Gemini free-tier quota.** The free tier allows about 20 generation requests per day per model,
  shared by manual Analyze, Run Automation and scheduled runs. It resets at midnight Pacific
  time (10:00 EAT during US daylight time, 11:00 EAT otherwise). An 08:00 EAT run uses the
  *previous* day's quota window, so if that quota was used up during the day, the scheduled run's
  drafts fail with `ai_unavailable`: the run is recorded as `FAILED`, exit 1. The invoices are
  retried on the next run. To give the scheduled run the full daily allowance, schedule it at
  11:30 EAT or later, or use a paid quota. Note that the Gemini adapter retries a 429 briefly
  (up to 3 attempts within 15s), which can't help with a daily limit.
- **Time zones.** Cron uses the system time zone (EAT), while "overdue" uses
  `DJANGO_TIME_ZONE` (UTC). Avoid 00:00–03:00 EAT, when the two disagree on the date.
- **Workflows accumulate.** Each scheduled run that produces drafts opens a new workflow. The
  `/automation` page shows the newest unfinished one; drafts from older runs stay in the
  dashboard approval queue. Use **Finish** to close handled workflows. Runs with no drafts
  finalize themselves.
- **Throughput.** At most `AUTOMATION_MAX_DRAFTS_PER_RUN` (10) drafts per run. Each run takes
  about 10s per Gemini call.
- **The database must be running.** Cron runs `run_automation` on the host against PostgreSQL
  (`POSTGRES_HOST`/`POSTGRES_PORT` in `backend/.env`, normally the Compose `db` on 127.0.0.1:5433).
  If `db` is down, the run fails (exit 1) and nothing is drafted.
- **No alerting.** Failures are visible only in the log and the app. Cron's mail is not used,
  because the wrapper redirects all output.

## 9. Verification record (Milestone 07)

> Historical record from before the PostgreSQL move (M08.1). The scratch databases below were
> SQLite files; the same procedure now uses a separate PostgreSQL database (section 6).

Done on 2026-10-19 on the development machine. Both verification passes used **isolated scratch
SQLite databases** (`SQLITE_PATH`), so the working `backend/db.sqlite3` was not modified (still
21 runs, 5 delivery attempts). Test data: the configured Google Sheet, which contains only
**fictional invoices** (INV-001…INV-007: "Acme Studio", "Nova Digital", "Bright Labs", "Alpha
Agency", "ABC Company", "FGH Corp", "MGH Corp"). Every recipient is on the developer-controlled
`GMAIL_ALLOWED_RECIPIENTS` list. At test time: 5 unpaid and overdue, 1 not yet due, 1 paid.
The real Google Sheets and Gemini APIs were used.

### 9.1 Pass A: one-shot cron entries, 21:16–21:21 EAT

Cron fired three entries: a fresh database at 21:16 and 21:20, and a snapshot of the real history
at 21:17.

```
Oct 19 21:16:01 linux CRON[19159]: (dires) CMD (SQLITE_PATH=…/fresh.sqlite3 … scheduled_automation.sh)
Oct 19 21:17:01 linux CRON[19514]: (dires) CMD (SQLITE_PATH=…/verify.sqlite3 … scheduled_automation.sh)
Oct 19 21:20:01 linux CRON[20554]: (dires) CMD (SQLITE_PATH=…/fresh.sqlite3 … scheduled_automation.sh)
```

| Run | Log summary |
| --- | --- |
| Fresh, 21:16 | `Sync SUCCEEDED: processed=7 created=7` · `Automation run 1 PARTIAL: evaluated=7 drafts_generated=2 skipped=2 failed=3` (3 × Gemini HTTP 503) · `No email was sent.` · `exit=0` |
| Snapshot, 21:17 | `Reasons: cooldown=5, not_overdue=1, not_unpaid=1` · `Automation run 22 COMPLETED: drafts_generated=0` (no Gemini calls) · `exit=0` |
| Fresh, 21:20 | `Reasons: ai_unavailable=1, draft_awaiting_review=2, …` · `Automation run 3 PARTIAL: drafts_generated=2 skipped=4 failed=1` · `exit=0` |

Afterwards the fresh database held 4 follow-ups, all `DRAFT`, one per invoice (INV-001, 002,
006, 007), and **0 delivery attempts**. Both automation runs had trigger `SCHEDULED`.

**Approval queue check** (Milestone 07, against that database): with Django on `:8001` and the
production Next.js build on `:3001`, `GET /api/backend/follow-ups` through the real proxy
returned 4 `DRAFT` reminders (INV-002, INV-006, INV-001, INV-007, generated 18:16–18:21 UTC),
each with `approved_at: null` and `sent_at: null`. `GET /api/backend/automation/workflows/current`
returned workflow 3: trigger `SCHEDULED`, state `WAITING_FOR_APPROVAL`, 0 deliveries,
`gmail_submissions_confirmed: 0`.

### 9.2 Pass B: short interval `*/2 * * * *`, 21:38–21:42 EAT

A temporary line `*/2 * * * * SQLITE_PATH=…/m07.sqlite3 COLLECTFLOW_AUTOMATION_LOG=…/m07.log
…/scheduled_automation.sh` was added to the crontab, ran for three firings, and was removed at
21:43:10. The crontab now matches the pre-test backup exactly (daily entry only).

```
Oct 19 21:38:01 linux CRON[26247]: (dires) CMD (… scheduled_automation.sh)
Oct 19 21:40:01 linux CRON[27003]: (dires) CMD (… scheduled_automation.sh)
Oct 19 21:42:01 linux CRON[27766]: (dires) CMD (… scheduled_automation.sh)
```

Each run synced successfully (`processed=7`, `created=7` on the first run then `unchanged=7`),
evaluated all 7 invoices (5 overdue), and attempted 5 drafts. **Every Gemini call returned
HTTP 429:** the free-tier daily limit (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`,
limit 20) had been used up by earlier QA calls that day, with a reset about 12 hours later. Each
run was recorded as `FAILED` with exit code 1, `GENERATING_DRAFTS = FAILED`, 15 `GenerationAttempt`
rows (`ai_unavailable`), **0 follow-ups and 0 delivery attempts**, and the lease released. This
pass therefore confirms scheduling, sync, evaluation, the `SCHEDULED` trigger, failure recording
and the no-Gmail guarantee, but not draft generation. Drafting under cron is covered by Pass A.

### 9.3 Results

| # | Requirement | Result | Evidence |
| --- | --- | --- | --- |
| 1 | Scheduler invokes the command without a dashboard action | ✅ Verified | 6 `CRON[...] CMD` syslog entries (Passes A and B) |
| 2 | Google Sheets synchronization executes | ✅ Verified | `Sync SUCCEEDED processed=7` in every run, with a `SYNC` run (trigger `SCHEDULED`) linked to each automation run |
| 3 | Eligible invoices are evaluated | ✅ Verified | Per-invoice items with reason codes (`not_unpaid`, `not_overdue`, `cooldown`, …); `invoices_overdue=5` |
| 4 | Gemini generates drafts for eligible invoices | ✅ Verified in Pass A (4 drafts). ⚠️ Not reproduced in Pass B (daily quota exhausted) | Pass A logs and DB; Pass B 429 records |
| 5 | Drafts appear in the approval queue | ✅ Verified | Proxy `GET /follow-ups` → 4 `DRAFT`; workflow `WAITING_FOR_APPROVAL` |
| 6 | No Gmail delivery occurs automatically | ✅ Verified | 0 `DeliveryAttempt` in every test database; all follow-ups `DRAFT` (sending requires `APPROVED`); `gmail_submissions_confirmed: 0` |
| 7 | `AutomationRun` records `SCHEDULED` | ✅ Verified | All 5 automation runs and their sync runs have trigger `SCHEDULED` |
| 8 | Repeat runs create no duplicate active drafts | ✅ Verified | Pass A 21:20 run: `draft_awaiting_review=2`; one active draft per invoice |
| — | Cooldown honoured | ✅ Verified | Pass A snapshot run: `cooldown=5`, no Gemini calls |
| — | Daily schedule restored | ✅ | `crontab -l` shows only `0 8 * * * …/scheduled_automation.sh` (plus pre-existing commented lines) |

**Overall: unattended scheduled automation is verified.** One caveat: draft generation depends
on Gemini quota being available at the scheduled time (see §8).
