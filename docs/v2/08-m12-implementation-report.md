# 08 — M12 implementation report: closed-loop UI and P0 acceptance gate

- **Date:** 2026-10-22
- **Baseline:** `151ccfe` (M11 deployed to the owner's stack)
- **Plan:** roadmap Phase 05 (`02-implementation-roadmap.md`) and the M12 authorization
- **Status:** implemented and tested on the development database with mocked Google Sheets, Gmail and
  Gemini. **No database migrations.** **Deployed** to the owner's stack on 2026-10-23 after approval
  (section 8.1).

| Gate | Result |
| --- | --- |
| Mocked P0 acceptance | **PASSED** (section 4) |
| Real-stack M12 deployment | **PASSED** (section 8.1) |
| Live provider end-to-end acceptance | **NOT YET COMPLETED** |

## 1. What changed

| Area | What it does now |
| --- | --- |
| Disputes page (`/disputes`) | Filters: open, open without an owner, mine, resolved, all (open first, oldest first). The list shows client, invoice, reason, age and owner. The detail shows: the invoice (amount, due date, status, link to its drawer); the originating reply (sender, date, the AI's suggestion, link to the Inbox); the case with owner, notes and resolution; and **whether the invoice is chased now**, listing every reason if not (this dispute, other holds, paid, paused...). Operators set the owner and add notes. Resolving is shown with the reason it is unavailable (`can_resolve`, computed by the backend) instead of a silently disabled button. No new statuses or rules. |
| Today (`/today`) | `GET /api/today/` builds eleven fixed sections from existing records only (no scores, no AI, no provider calls), each in deterministic order: integration problems; replies to review (oldest first); open disputes (unassigned first); unknown delivery outcomes; drafts awaiting approval; approved but not sent; broken promises (last 14 days, still unpaid); promised payments due; tasks due today or overdue; sequences needing a person (unknown or failed delivery, failed AI draft, history review); invoices on hold (with each hold's release rule). Each item says why it is there, who can act, links to the screen where it is done, and, when the signed-in user cannot act (e.g. a firm draft for an operator), why. Refreshes every minute. |
| Tasks page (`/tasks`) | Open, mine, done, cancelled or all; open tasks by due time. Assign, done with a note, cancel with a note (call tasks explain that they cannot be cancelled). Links to each task's invoice. |
| Linked records | Deep links `/?invoice=`, `/inbox?reply=`, `/disputes?case=`, `/tasks?task=`. The drawer's holds link to the owning reply or dispute; the Inbox links to the invoice, the recorded promise or dispute, and the reply's tasks; the Disputes page links to the invoice and reply; Today and Tasks link everywhere. Opening and closing the drawer keeps the URL in step, so links can be shared. |
| Navigation | Today · Invoices · Inbox (count) · Disputes · Tasks · Sequences · Automation · Integrations. |
| States | New screens have loading, empty and error states. Today and the Disputes page keep the last loaded data when a refresh fails and say so. The existing dashboard already flags stale sheet data (26 h). |
| API additions (no breaking changes) | `GET /api/today/`; disputes gain `?status=RESOLVED`, `?owner=me`, `?unassigned=true`, open-first ordering, and per-case `chasing`, `can_resolve`, invoice facts and `reply_summary`. Existing fields and endpoints are unchanged. |

## 2. Workflow audit: invoice to resolution

| Step | Where an operator sees it | What explains a block |
| --- | --- | --- |
| Invoice | Invoices table (sequence note, "On hold"), drawer | Holds list with the release rule and a link to the owner |
| Sequence | Drawer step strip; Today "sequences that need a person" | `blocked_reason` and history review explained in operator terms |
| Draft | Approval queue, drawer, Today "drafts" | Four-eyes reasons and why *this* user cannot approve (`approval` block, Today `blocked`) |
| Approval → delivery | Drawer delivery panel; Today "approved, not sent" and "unknown outcome" | Unknown outcomes say "never resent; reconcile" |
| Reply → classification | Inbox (state, AI label, confidence, triage reason) | Approver requirement for resuming after a promise, dispute or payment claim |
| Promise, dispute, task | Drawer case section, Disputes page, Tasks page, Today | `chasing.blocked_by`, `can_resolve.reason`, the release rule per hold |

## 3. Safety audit of outbound paths

Each guarantee is enforced in the backend, and each is exercised by a test.

| Path | Enforced by | Covered by |
| --- | --- | --- |
| Draft request | `check_can_generate` → `refuse_if_ineligible` (holds, dispute, paid, removed, invalid) + sequence planner (paused, review, unknown delivery) | P0 test 8, `cases/test_disputes`, `followups/test_api` |
| Draft save (generation) | Same rule again under the invoice row lock | P0 test 8; M10 race test (reply during a running tick) |
| Approval | Eligibility, snapshot, four-eyes (`approval.refusal`) under the follow-up and invoice locks | P0 tests 4 and 8; concurrency test (two approvers → one approval) |
| Send reservation | Eligibility under the invoice lock + conditional `APPROVED → SENDING` + one SENDING attempt per follow-up | P0 test 8; concurrency (three sends → one attempt); dispute-vs-reservation race |
| Worker delivery | Re-checks eligibility, recipient, content and Gmail readiness before the single `send` (`num_retries=0`); `max_attempts=1`; recovered jobs fail, never re-run | P0 tests 12 and 13 |
| Sequence tick / automation | Same eligibility; never approves or sends (architecture test: scheduled apps cannot import delivery or the Gmail send client) | P0 test 8; `core/tests_architecture`; scheduling tests |
| Queued jobs | Delivery jobs run once; classify jobs are keyed by attempt; poll, tick, monitor and purge are keyed by slot | P0 test 13 |
| Holds | One owner per hold (database constraint); only the owner releases it | P0 test 9; `cases/test_disputes` overlapping holds |
| Payment claims | RECONCILE task plus hold; the invoice status only ever comes from the sheet | P0 test 10 |
| Unknown delivery | `DELIVERY_UNKNOWN` blocks the step, refuses a new send and a regeneration; resolved only by `reconcile_delivery` | P0 test 12 |

No gaps were found that needed a code change. The audit is recorded here and in the acceptance suite.

## 4. P0 acceptance suite (`apps/core/tests_p0_acceptance.py`)

The whole loop runs through the real services with a fake Google Sheets client, fake Gmail send and
read clients, and a fake Gemini. The real Gmail send client is patched to fail if anything bypasses
the fakes.

| # | Scenario | Result |
| --- | --- | --- |
| 1–5 | Sheet sync (paid rows not enrolled) → enrollment → friendly draft → operator edit → approval → delivery through fake Gmail; the step is recorded the moment it is sent | pass |
| 4 | High-value draft: an operator is refused; an approver who edited it is refused (four-eyes); a second approver approves | pass |
| 6–7 | Reply stored once despite repeated and replayed polls; promise reply goes to review (the AI creates nothing); a person confirms the promise; the promise owns the hold; drafting is refused | pass |
| 8–9 | With an approved step-2 reminder in place, a dispute reply plus case withdraws it. Draft request, generation, send reservation and the tick (no Gemini call) are refused. Resolution "invoice stands" reports chasing still blocked by an unrelated reply hold. Once that reply is handled, chasing resumes, and step 2 was never sent | pass |
| 10 | Payment claim → RECONCILE task, hold, invoice still UNPAID; finishing the task does not release the hold; an operator cannot release it | pass |
| 11 | Call task done three times (with ticks between) → step 4 completed once, sequence COMPLETED | pass |
| 12 | Gmail outcome unknown → `DELIVERY_UNKNOWN`; worker rerun, stale recovery, tick, a new send request and regeneration: Gmail called exactly once | pass |
| 13 | Three rounds of polls with the cursor reset, ticks and double scheduling: one reply, one task, one classify job, one reminder; slot keys unique; a crashed delivery job is failed, not re-run | pass |
| 14 | AI proposal (low confidence), human correction and dispute decision kept in order in the append-only log (an update is refused); the timeline holds the full trail from sync to dispute | pass |
| — | Concurrency (PostgreSQL threads): two approvers → one approval; three send requests → one delivery attempt; dispute racing an approval → the reminder is withdrawn or refused, and can never be reserved | pass |

## 5. Tests and checks

- **Backend:** the full suite on PostgreSQL 16: **500 tests, all passing** (484 before M12, plus the 11
  P0 acceptance tests and 5 Today/dispute-list tests). Two CSRF and role tests added afterwards
  pass on their own run (`apps.core.tests_today`, 7 tests), so **502 tests** in total.
- **Django:** `manage.py check` clean; `makemigrations --check` reports no changes (M12 has no
  migrations).
- **Frontend:** `tsc --noEmit` and `eslint` clean; the production image (`next build`) builds (under a
  separate tag, then removed, so the running stack was untouched).
- **Proxy allowlist:** 62/62 frontend API calls allowed (scripted check).

## 6. Security review

- **Authorization:** every new endpoint uses the default `IsOperator` (session plus role). Approver-only
  actions (dispute resolution, promise cancellation, resuming after protected holds) are enforced in the
  services and covered by API tests (`403 approver_required`). `/api/today/` refuses users without a role.
- **CSRF:** writes to the M11/M12 endpoints without a CSRF token are refused (`403 csrf_failed`) and
  create nothing (new test). The web proxy forwards only the session and CSRF cookies plus
  `X-CSRFToken`, and still checks the same origin for writes.
- **Proxy allowlist:** all 62 API calls the frontend makes were checked against the allowlist by script
  (0 missing); `/api/today/` was added.
- **Validation:** all inputs are validated server-side (dates, amounts, reasons, notes, assignees); the
  frontend only mirrors the rules.
- **Data exposure:** the Today and dispute responses carry invoice and reply metadata only to signed-in
  users with a role. `/api/users/` exposes no email addresses.

## 7. Deviations and remaining gaps

| Item | Status |
| --- | --- |
| Roadmap "sequence editor" for M12 | The basic version editor (M09, admins) is unchanged; a richer editor was not in the M12 authorization |
| P0 demo against live providers | Covered by the mocked acceptance suite; the live owner checks (M10 reply test) remain manual |
| Approved-reminder sending from Today | Today links to the drawer; sending stays a deliberate action there |
| Real-time updates | Pages refresh on actions and every minute (Today), not by push |
| Browser end-to-end tests (Playwright) | Planned for M15 (QA); M12 verification is API-level plus typecheck, lint and the production build |
| Partial payments, dispute notifications by email | Unchanged (M11 limitations) |

## 8. Deployment plan

M12 has **no migrations**, so deployment is a rebuild of the api, worker and web images:

1. Back up the database and verify the backup (restore and compare, as for M10 and M11).
2. Tag the running images `pre-m12`; build the new images while the old stack serves.
3. Pause the worker; `docker compose up -d --no-deps --no-build api web worker`.
4. Verify through the web proxy: `/api/today/`, `/api/disputes/?status=RESOLVED`, the `/today`,
   `/disputes` and `/tasks` pages, and the existing pages; confirm no sends and no Gemini calls.

Rollback: redeploy the `pre-m12` images (no schema change to reverse).

### 8.1 Production deployment record (2026-10-23, approved)

Commit `0235e2d`. Worker paused 05:43:05Z, api and web swapped, worker resumed 05:46:29Z (UTC).

| Check | Result |
| --- | --- |
| Before | HEAD `0235e2d`, clean tree; no migration files changed since M11 (`151ccfe`). Stack on the M11 images; 0 pending or running outbox jobs; automation lease free. Data: 8 invoices (7 unpaid, 1 paid), 7 follow-ups (6 SENT, 1 REJECTED), 6 delivery attempts (last 2026-10-22 17:22Z), 10 generation attempts (last 2026-10-22 19:00Z), 6 active enrollments, 1 reply (INV-008, ACTIONED), 1 hold (released), 1 open task (INV-008 information request), 0 promises, 0 disputes, 19 timeline events |
| Backup | `backend/backups/collectflow-before-m12-20261023T052134.dump` (164 KB, mode 600, SHA-256 in `.sha256`, verified). Restored into a scratch database: 36/36 tables with identical row counts, 19 key tables with identical content hashes, the 4 append-only triggers present. Scratch database dropped |
| Rollback images | `collectflow-{api,worker,web}:pre-m12` = the running M11 images (`2d146d67fe11`, `4febf38d6307`, `2eaa75a114d0`) |
| Configuration recorded | Fingerprints of `backend/.env`, the compose service configuration, the worker's Gmail, recipient and automation settings (`GMAIL_READ_ENABLED=true`) and the credential files, including the Gmail token. Identical after deployment |
| Build | New images built while the old stack served: api `0edc57667052`, worker `137cc5023d76`, web `1b514d58a643`. The first attempt failed on a PyPI download timeout (the base image had to be pulled again); the retry succeeded. The images contain the `/today`, `/disputes` and `/tasks` pages and `apps/core/today.py` |
| Swap | Worker stopped with 0 jobs in flight; `up -d --no-deps --no-build api web`; api healthy; "No migrations to apply" (still 47 applied); `manage.py check` clean |
| Pages (web) | `/`, `/today` (title "Today · CollectFlow AI"), `/disputes`, `/tasks`, `/inbox`, `/sequences`, `/settings/integrations`, `/login` and the deep links `/?invoice=`, `/tasks?task=`, `/inbox?reply=` for the real INV-008 records: 200 |
| Proxy | `today`, `disputes` (`status=RESOLVED`, `unassigned=true`), `tasks?open=true`, `invoices`, `replies`, `promises`, `me` reach the backend (401 signed out, so allowlisted); an unknown route 404; a cross-origin write 403 |
| API (signed-in user) | Run inside the api container by passing requests straight to the views with the real user attached in memory (no session or login created), in a transaction that was rolled back. `/api/today/`, all dispute and task filters, invoices, replies, promises, integrations, enrollments, follow-ups, dashboard, users, me: 200. Today listed the INV-008 information-request task (overdue) linking to `/tasks?task=…`. During the pause it also showed the stopped worker as an integration problem; that cleared after the resume |
| Two-way links | Task → invoice INV-008 and → its reply; the invoice's task list → the task; reply → invoice. INV-008: UNPAID, no active holds, no promise or dispute |
| Permissions | A user without a role: `/api/today/` 403. An operator: `/api/today/` 200; resolving a dispute and cancelling a promise 403 `approver_required` (refused before any lookup) |
| CSRF | Authenticated writes without a token to `/api/tasks/`, `/api/disputes/`, `/api/promises/`, dispute resolve and promise cancel: 403 `csrf_failed`. All aimed at nonexistent records; dispute, promise and task counts unchanged (0, 0, 1) |
| Record preservation | Before and after (and before the worker resumed): identical row counts and content hashes for invoices, clients, holds, follow-ups, delivery and generation attempts, enrollments, sequences and steps, replies, classifications, tasks, promises, disputes, dispute notes, timeline, users and profiles; identical workflow states. The only changes are scheduler records: 7 outbox jobs, 2 sheet-sync runs, the mailbox cursor and the worker heartbeat |
| Worker | During the build the M11 worker ran 4 reply polls (0 ingested) and 1 tick (0 drafts). After the resume, the M12 worker: `sequences.tick` SUCCEEDED (0 generated), `inbox.poll` SUCCEEDED (0 ingested). No duplicate idempotency keys, nothing pending or stuck, no errors in the log |
| External providers | 0 Gmail sends (no delivery attempts) and 0 Gemini calls (no generation attempts) since before the deployment. Only the read-only scheduled reply polls touched Gmail, as already enabled |
| Health | Sheets, Gmail sending, Gmail read, reply polling, Gemini and worker healthy; all four scheduled jobs healthy |

Not exercised on real records, to avoid manufacturing activity: approver-only refusal on an existing
dispute or promise (none exist; covered by the API tests and the P0 suite), and the dispute detail view
(no disputes). No drafts were approved, no emails sent, and no promises or disputes created or changed.

**Final status:** deployed and verified. Rollback stays ready: pause the worker, retag the `pre-m12`
images as `latest`, then `docker compose up -d --no-deps --no-build api web worker`. No database restore
is needed (M12 changed no schema).
