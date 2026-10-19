# Scheduled (unattended) automation: setup and verification

- **Date:** 2026-10-19
- **Machine:** Linux, cron daemon `/usr/sbin/cron` active, system time zone Africa/Addis_Ababa (EAT, +03:00)

## 1. Starting point

| Question | Finding before this change |
| --- | --- |
| Does `run_automation` exist? | Yes. `apps/automation/management/commands/run_automation.py` calls `AutomationOrchestrator().run(trigger=SCHEDULED)`. Exit codes: 0 completed/partial, 1 failed, 2 lease held. It supports `--dry-run`. |
| Is a scheduler installed? | **No.** The cron daemon was running, but the user crontab had no CollectFlow entry (only two commented-out Kafka lines), and there were no systemd timers. The README described a cron line that had never been installed. |
| Evidence of earlier "scheduled" runs | `backend/logs/automation.log` held one hand-run test from 2026-10-16 (`scheduled exit=0`), not a cron execution. |

**Conclusion:** proactive automation existed but was not genuinely unattended.

## 2. What was added

- [`backend/scripts/scheduled_automation.sh`](../backend/scripts/scheduled_automation.sh): a
  POSIX `sh` wrapper for cron. It `cd`s into `backend/`, runs
  `.venv/bin/python manage.py run_automation`, and appends the output to
  `backend/logs/automation.log` between `started` and `finished, exit=N` timestamp lines. It
  returns the command's exit code. It has **no logic of its own**: no new services, models or
  settings. `COLLECTFLOW_AUTOMATION_LOG` can redirect the log (used for verification).
- A user-crontab entry (installed on this machine):

  ```cron
  # collectflow-ai: daily proactive automation (drafts only, never sends email)
  0 8 * * * /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh
  ```

  The first unattended run against the real database is due **2026-10-20 at 08:00 EAT**
  (05:00 UTC, the same calendar date as `DJANGO_TIME_ZONE=UTC`).

Because the wrapper only invokes the existing command, scheduled runs reuse:

- `AutomationOrchestrator`: sync → evaluate → draft, with `WorkflowStep` progress.
- `AutomationLease`: one run at a time, shared with the **Run Automation** button. A collision
  exits with code 2.
- The eligibility and cooldown policy (`apps/automation/policy.py`), plus
  `FollowUpGenerationService`'s lock and the partial unique constraint (one active reminder per
  invoice).
- The audit trail: `AutomationRun` (trigger `SCHEDULED`) with its own `SYNC` run,
  `AutomationRunItem` per invoice and `GenerationAttempt` per Gemini call.
- No Gmail: the orchestrator never calls Gmail (enforced by tests). Drafts wait for approval and
  an explicitly confirmed send.

## 3. Verification: cron-triggered runs on fictional invoices

**Test data.** The configured Google Sheet contains only fictional invoices: INV-001…INV-007
for "Acme Studio", "Nova Digital", "Bright Labs", "Alpha Agency", "ABC Company", "FGH Corp" and
"MGH Corp". Every `client_email` is on the developer-controlled `GMAIL_ALLOWED_RECIPIENTS`
allowlist. At the time of the test: 5 were unpaid and overdue, 1 not yet due (INV-003) and 1 paid
(INV-004).

**Isolation.** To leave the working database untouched, the runs used two scratch SQLite
databases via `SQLITE_PATH` (variables set in the cron line override `backend/.env`):

- `fresh.sqlite3`: newly migrated, with no history.
- `verify.sqlite3`: a consistent snapshot (`sqlite3` backup API) of the real `db.sqlite3`,
  including its sent-reminder history.

The real Google Sheets and Gemini APIs were used, with the real Gmail configuration loaded.

**Trigger.** Three one-shot entries were added to the user crontab (tagged
`collectflow-verify`), started by the cron daemon with no human involvement, and then removed:

```cron
16 21 19 10 * SQLITE_PATH=…/fresh.sqlite3  COLLECTFLOW_AUTOMATION_LOG=…/fresh.log    …/scheduled_automation.sh
17 21 19 10 * SQLITE_PATH=…/verify.sqlite3 COLLECTFLOW_AUTOMATION_LOG=…/snapshot.log …/scheduled_automation.sh
20 21 19 10 * SQLITE_PATH=…/fresh.sqlite3  COLLECTFLOW_AUTOMATION_LOG=…/fresh.log    …/scheduled_automation.sh
```

### 3.1 Cron daemon evidence (`/var/log/syslog`)

```
Oct 19 21:16:01 linux CRON[19159]: (dires) CMD (SQLITE_PATH=…/fresh.sqlite3 … scheduled_automation.sh)
Oct 19 21:17:01 linux CRON[19514]: (dires) CMD (SQLITE_PATH=…/verify.sqlite3 … scheduled_automation.sh)
Oct 19 21:20:01 linux CRON[20554]: (dires) CMD (SQLITE_PATH=…/fresh.sqlite3 … scheduled_automation.sh)
```

### 3.2 Run 1: fresh database, first unattended run (21:16)

```
=== 2026-10-19T21:16:01+03:00 scheduled run_automation started (pid 19160)
Gemini server error (HTTP 503)
Follow-up generation failed: ai_unavailable
Gemini server error (HTTP 503)
Follow-up generation failed: ai_unavailable
Gemini server error (HTTP 503)
Follow-up generation failed: ai_unavailable
Sync SUCCEEDED: processed=7 created=7 updated=0 unchanged=0 rejected=0
Reasons: ai_unavailable=3, not_overdue=1, not_unpaid=1
Automation run 1 PARTIAL: evaluated=7 drafts_generated=2 skipped=2 failed=3
3 draft(s) could not be generated; see run items.
Drafts generated — awaiting approval. No email was sent.
=== 2026-10-19T21:17:40+03:00 finished, exit=0
```

Run items: INV-007 and INV-001 `GENERATED`; INV-006, INV-005 and INV-002 `FAILED ai_unavailable`
(Gemini was overloaded with HTTP 503 and the bounded retry gave up); INV-003 `not_overdue`;
INV-004 `not_unpaid`. Steps: sync, evaluate and generate `COMPLETED`; finalize `PENDING`
(drafts await review).

### 3.3 Run 2: fresh database, second unattended run (21:20). No duplicates.

```
=== 2026-10-19T21:20:01+03:00 scheduled run_automation started (pid 20555)
Network error contacting Gemini
Follow-up generation failed: ai_unavailable
Sync SUCCEEDED: processed=7 created=0 updated=0 unchanged=7 rejected=0
Reasons: ai_unavailable=1, draft_awaiting_review=2, not_overdue=1, not_unpaid=1
Automation run 3 PARTIAL: evaluated=7 drafts_generated=2 skipped=4 failed=1
1 draft(s) could not be generated; see run items.
Drafts generated — awaiting approval. No email was sent.
=== 2026-10-19T21:21:10+03:00 finished, exit=0
```

- INV-007 and INV-001 were **skipped as `draft_awaiting_review`**: existing drafts were not
  regenerated or duplicated.
- INV-006 and INV-002 (failed in run 1) were drafted this time. INV-005 failed again (network
  error) and will be retried on the next scheduled run.

Final state of `fresh.sqlite3`:

| Check | Result |
| --- | --- |
| Follow-ups | 4 × `DRAFT` (INV-001, INV-002, INV-006, INV-007), exactly one per invoice |
| `DeliveryAttempt` rows | **0** (nothing was sent or attempted) |
| `GenerationAttempt` | 4 succeeded, 4 failed (all recorded) |
| Automation runs | 2 × trigger `SCHEDULED`, each linked to its own `SYNC` run, with per-invoice items |
| Lease | released after each run |

### 3.4 Run 3: snapshot of real history, unattended run (21:17). Cooldown honoured.

```
=== 2026-10-19T21:17:01+03:00 scheduled run_automation started (pid 19516)
Sync SUCCEEDED: processed=7 created=0 updated=0 unchanged=7 rejected=0
Reasons: cooldown=5, not_overdue=1, not_unpaid=1
Automation run 22 COMPLETED: evaluated=7 drafts_generated=0 skipped=7 failed=0
=== 2026-10-19T21:17:18+03:00 finished, exit=0
```

All five overdue invoices had a reminder confirmed `SENT` within `FOLLOWUP_COOLDOWN_DAYS` (7), so
they were skipped as `cooldown` and **no Gemini call was made**. The run finalized itself, since
nothing needed review. `DeliveryAttempt` stayed at 5 and follow-ups stayed at 5 × `SENT` and
1 × `REJECTED`, both unchanged.

### 3.5 What this proves

| Requirement | Evidence |
| --- | --- |
| Genuinely unattended | Started by the cron daemon (syslog `CRON[...] CMD`) in cron's minimal environment |
| Reuses the orchestrator | Output and records come from `run_automation` → `AutomationOrchestrator` (trigger `SCHEDULED`, `WorkflowStep`s) |
| Duplicate prevention | Run 2 skipped existing drafts (`draft_awaiting_review`); one active reminder per invoice |
| Cooldown policy | Run 3 skipped all recently-sent invoices (`cooldown`) |
| Audit history | `AutomationRun` + `SYNC` run + `AutomationRunItem` + `GenerationAttempt` for every run |
| Never sends Gmail | 0 delivery attempts created in either database; drafts only, "No email was sent." |
| Resilience | Gemini 503/network errors were recorded as `FAILED ai_unavailable`, the run continued (`PARTIAL`, exit 0), and they were retried on the next run |

The working database `backend/db.sqlite3` was not modified by this verification.

## 4. Operating it

**Check status**

```bash
crontab -l | grep -A1 collectflow-ai           # is it scheduled?
tail -n 30 backend/logs/automation.log          # what happened at each run
grep CRON /var/log/syslog | grep scheduled_automation   # did cron fire it?
python manage.py run_automation --dry-run       # what the next run would draft (no changes)
```

Scheduled runs also appear in the dashboard's automation panel and in the `/automation`
workspace (labelled "scheduled run"). New drafts appear in the dashboard approval queue.

**Change the time:** run `crontab -e` and edit `0 8 * * *` (minute, hour). For example,
`30 7 * * 1-5` runs at 07:30 on weekdays.

**Disable** (removes only the CollectFlow lines):

```bash
crontab -l | grep -v -e 'collectflow-ai' -e 'scheduled_automation.sh' | crontab -
```

To pause temporarily, put `#` in front of the line with `crontab -e`. Disabling the schedule
doesn't affect the **Run Automation** button or existing drafts.

**Re-enable:** see the README section "Scheduling with cron (Linux)".

## 5. Limitations

- Cron doesn't catch up. If the machine is off or asleep at 08:00, that day's run is skipped.
  The next run drafts whatever is eligible at that point.
- Cron uses the system time zone (EAT) and overdue checks use `DJANGO_TIME_ZONE` (UTC). At
  08:00 EAT both are on the same date. Avoid scheduling between 00:00 and 03:00 EAT, when the
  dates differ.
- Each scheduled run that produces drafts opens a new workflow. The `/automation` workspace
  shows the newest unfinished one, while drafts from older runs remain in the dashboard approval
  queue. Use **Finish** in the workspace to close workflows you have handled.
- Gemini availability varies (during this verification it returned HTTP 503 and network
  errors). Failed drafts are recorded and retried on the next run, not immediately.
- A Gmail authorization that has expired doesn't affect scheduled runs, because they never use
  Gmail. It only matters when an operator sends.
