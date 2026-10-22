# 00 — Repository audit

- **Date:** 2026-10-22
- **Commit audited:** `676b43c` ("Foundation v2: users, timeline, outbox worker (Milestone 08)").
  **Updated for M08.1** (PostgreSQL 16 and Docker Compose); see `04-m08-postgresql-migration-report.md`.
- **Specifications:** *CollectFlow AI Solution Design* (v1, implemented) and *CollectFlow AI Solution Design v2* (approved)
- **Method:** read both specifications, then the code, migrations, tests and docs. Every statement below
  is verified against the repository unless it is marked **Assumption**.

## 1. Summary

The repository implements all of v1 and the v2 foundation (v2 Phase 01, delivered as Milestone 08).
The v2 collections loop (sequences, threading, replies, promises, disputes) is not started yet.

| Area | State |
| --- | --- |
| v1 scope (sync, drafts, approval, Gmail send, automation, workspace, cron) | Implemented, with three small v1 gaps (section 9) |
| v2 foundation: sessions and roles, outbox and worker, timeline, clients | Implemented in `676b43c` |
| v2 database and runtime | PostgreSQL 16 and Docker Compose (db, api, worker, web), M08.1. The earlier SQLite decision was reversed |
| v2 P0 loop: sequences, threading, four-eyes, replies, promises, disputes, tasks | Not started |
| v2 P1: risk, worklist, statements, analytics, forecast | Not started |
| v2 quality: AI evals, Hypothesis, import-linter, Playwright, CI | Not started |

## 2. Backend

Python 3.12, Django 5.2, DRF 3.15, PostgreSQL 16 via psycopg 3 (`POSTGRES_*` settings). Settings:
`backend/config/settings.py`. Docker Compose: `docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`.

### 2.1 Django apps

| App | Responsibility | Key files |
| --- | --- | --- |
| `apps/core` | Health check, API error format `{code, detail, fields?}` | `views.py`, `exceptions.py` |
| `apps/accounts` | `Profile.role` (OPERATOR < APPROVER < ADMIN), session login with CSRF, Argon2, login rate limit, `create_user` command | `models.py`, `roles.py`, `permissions.py`, `views.py`, `authentication.py` |
| `apps/timeline` | Append-only `TimelineEvent`, the single writer `record()/build()`, database triggers (PostgreSQL: UPDATE, DELETE, TRUNCATE) | `models.py`, `services.py`, `migrations/0001_initial.py` |
| `apps/invoices` | `Client`, `Invoice`, `AutomationRun`; sheet parsing and all-or-nothing sync | `models.py`, `sheet_parser.py`, `sheets_client.py`, `sync.py` |
| `apps/followups` | `FollowUp` state machine, eligibility, prompts, generation, approval, Gmail delivery, reconciliation | `models.py`, `eligibility.py`, `prompts.py`, `services.py`, `delivery.py` |
| `apps/automation` | Outbox jobs and worker, orchestrator, policy, lease, interactive workflow | `outbox.py`, `orchestrator.py`, `policy.py`, `lease.py`, `workflow.py` |
| `integrations/` | Provider adapters: `gemini.py` (google-genai, JSON schema, bounded retry), `gmail.py` (send only, never retried) | |

The v2 apps `sequences`, `inbox` and `insights` do not exist yet.

### 2.2 Models and migrations

All migrations are committed and match the models (`makemigrations --check` reports no changes).

| Model | Notes |
| --- | --- |
| `Client` | UUID, unique lowercased `email` (check constraint), `display_name`. No metrics or risk fields yet. |
| `Invoice` | UUID, unique `external_id`, FK `client`, `amount > 0` and status check constraints, `in_sheet`, `last_synced_at`. `save()` attaches a client when none is set. |
| `AutomationRun` | Kind `SYNC` or `AUTOMATION`; trigger `MANUAL`, `COMMAND` or `SCHEDULED`; counts and row errors. There are no `SEQUENCE_TICK` or `REPLY_POLL` kinds yet. |
| `FollowUp` | 7-state machine; partial unique "one active per invoice"; `invoice_snapshot`; `prompt_version`, `last_edited_by`, `approved_by`. No `enrollment`, `step`, `kind` or `thread_reply_to`. |
| `GenerationAttempt`, `DeliveryAttempt` | Sanitized errors. `DeliveryAttempt` has `requested_by`, `gmail_message_id`, `gmail_thread_id` and a partial unique "one SENDING per follow-up". No RFC `Message-ID` is stored. |
| `AutomationRunItem`, `WorkflowStep`, `AutomationLease` | v1 workflow, unchanged |
| `OutboxJob`, `WorkerHeartbeat` | Unique `idempotency_key`, attempts ≤ `max_attempts` check |
| `TimelineEvent` | UUID; FKs to invoice, client and actor (PROTECT); `actor_type` USER/SYSTEM/SCHEDULER; `type` enum; JSON payload. Triggers block UPDATE and DELETE. |
| `Profile`, `LoginFailure` | Roles and rate limiting |

Migration chain: invoices `0001–0004` (0004 backfills clients and `in_sheet`), followups `0001–0003`,
automation `0001–0004`, accounts `0001`, timeline `0001`. The local database is migrated; a copy from
before 0004 was taken when M08 was applied.

### 2.3 API (all under `/api/`)

Public: `health/`, `auth/csrf/`, `auth/login/`, `auth/logout/`. Everything else requires a session with
at least `OPERATOR`; writes need `X-CSRFToken`.

| Group | Endpoints |
| --- | --- |
| Session | `GET me/` |
| Invoices | `POST sync/` (202 + run and job), `GET invoices/`, `GET invoices/{id}/`, `GET dashboard/`, `GET automation-runs/`, `GET automation-runs/{id}/` |
| Follow-ups | `POST invoices/{id}/analyze/` (202 + job), `GET follow-ups/`, `GET/PATCH follow-ups/{id}/`, `POST follow-ups/{id}/approve|reject/`, `POST follow-ups/{id}/send/` (202 + job and attempt), `GET follow-ups/{id}/delivery-attempts/`, `GET integrations/gmail/status/` |
| Automation | `POST automation/run/` (202 + job), `GET automation/workflows/current|{id}|{id}/steps|{id}/pending-actions/`, `POST automation/workflows/{id}/finalize/`, `POST automation/workflows/{id}/items/{item}/leave-unsent/` |
| Jobs and history | `GET jobs/{id}/`, `GET timeline/?invoice=&client=` |

Management commands: `sync_invoices`, `run_automation [--dry-run]`, `run_worker [--once]`,
`authorize_gmail`, `reconcile_delivery`, `create_user`.

### 2.4 Integrations

| Provider | Implementation | v2 relevance |
| --- | --- | --- |
| Google Sheets | Service account, `spreadsheets.readonly`, 30 s timeout, no transaction open during the fetch (`sheets_client.py`) | Reused as is |
| Gemini | `google-genai`, JSON schema output, tone pinned in the schema, 2 retries within 15 s on 429/5xx (`integrations/gemini.py`); prompt `draft-v1` (`followups/prompts.py`) | Needs sequence and broken-promise context; a new classification prompt and schema |
| Gmail | OAuth user token; scopes `gmail.send` plus identity scopes; `check_configured()` (local) and `check_ready()` (network); `send(recipient, subject, body)` builds a plain MIME message, `num_retries=0`, and maps outcomes to rejected or unknown (`integrations/gmail.py`) | Needs `threadId`, `In-Reply-To` and `References` on send, and a read scope for replies |

### 2.5 Follow-up lifecycle and rules (verified)

- Eligibility (`followups/eligibility.py`): in the sheet, UNPAID, due before the business date, valid email,
  positive amount. Tone comes from days overdue (1–7 friendly, 8–14 professional, 15+ firm). v2 replaces
  this with tone from the sequence step.
- Automation policy (`automation/policy.py`): active follow-up, invoice rules, last FAILED, cooldown after
  SENT, recently REJECTED. Sequences supersede this.
- Approval re-reads the invoice and refuses stale snapshots. Approval is open to any role: **no four-eyes
  rule yet**.
- Delivery (`followups/delivery.py`): the request validates and reserves (conditional UPDATE, attempt row,
  timeline event and job in one transaction). The worker re-validates, runs `check_ready`, then calls Gmail
  once. If anything fails before submission, the reservation is released back to APPROVED. Outcomes are
  SENT, FAILED or DELIVERY_UNKNOWN; reconciliation is CLI only.

### 2.6 Automation and scheduling

- `AutomationOrchestrator` takes the lease, then syncs, evaluates and drafts (at most
  `AUTOMATION_MAX_DRAFTS_PER_RUN`). It never imports delivery. Manual runs go through an `automation.run`
  job; cron runs `scripts/scheduled_automation.sh` → `run_automation`, daily at 08:00 EAT
  (`docs/SCHEDULING.md`).
- `run_worker` claims jobs one at a time, writes a heartbeat every 10 s, recovers stale jobs and retries
  with backoff (delivery jobs never retry).
- There is no 15-minute sequence tick, no 5-minute reply poll and no daily promise check yet.

## 3. Frontend

Next.js 16 App Router, React 19, TypeScript 5, Tailwind 4. No TanStack Query or Recharts yet.

| Route | Contents |
| --- | --- |
| `/login` | `components/auth/LoginForm.tsx` |
| `/` | `components/dashboard/*`: summary cards, sync status, invoice table (status filter tabs), follow-up drawer (draft editor, approve, reject, withdraw, send panel with confirmation), approval queue, automation panel |
| `/automation` | `components/automation/*`: step tracker, review, send confirmation, deliveries, summary, Finish |
| `/api/backend/[...path]` | Proxy: per-method route allowlist, same-origin check on writes, 20 KB body cap, forwards only `sessionid`, `csrftoken` and `X-CSRFToken`, relays `Set-Cookie` |

`src/lib/api/*` contains typed clients with runtime validators. `jobs.ts` polls jobs every 1.5 s and reports
when the worker is down. `SessionGate` and `UserMenu` handle the session.

v2 routes not present yet: `/invoices`, `/invoices/[id]`, `/clients`, `/inbox`, `/approvals`, `/disputes`,
`/analytics`, `/sequences`, `/settings/integrations`.

## 4. Security (verified)

- No committed secrets. `backend/credentials/`, `.env*`, `*.sqlite3` and `backend/logs/` are gitignored.
- Sessions: `HttpOnly`, `SameSite=Strict`; `Secure` outside debug. CSRF is required on every write,
  including login. Five failed logins per username in 15 minutes return 429.
- `/admin/` is mounted only with `ENABLE_ADMIN=true`.
- The browser holds no credential. The proxy holds no secret and only forwards session cookies.
- The recipient allowlist is mandatory in debug; an empty list means no sends.
- Logs contain IDs and codes only. Timeline payloads contain IDs, codes, changed field values and the
  recipient address. Email bodies are never stored there.
- One exception to "no provider calls in web requests": `GET /api/integrations/gmail/status/` runs
  `check_ready()` (token refresh plus tokeninfo) inside the request. It is read-only and moves to the health
  page in v2 Phase 03.

## 5. Tests and checks (run on 2026-10-22)

| Check | Result |
| --- | --- |
| `python manage.py test` | 256 tests OK at audit time on SQLite. After M08.1: 269 tests on PostgreSQL 16 (see the M08.1 report) |
| `python manage.py check` | No issues |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `npm run lint`, `npm run typecheck` | Both exit 0 with no findings |

Coverage highlights: parser and sync (all-or-nothing, removal and return, clients, timeline events),
generation (schema, tone, links, retries, transaction boundary), delivery (every outcome, release on not
ready or stale, reconciliation), outbox (claim, retry, abandon, idempotency), threaded concurrency (each job
claimed once; Gmail called at most once for parallel sends), route-wide authorization (401 without a
session, 403 without a role), the login flow with real CSRF, and the clients/in_sheet backfill migration.
Automation tests replace `GmailClient.send` with a function that fails the test if called.

Not present: Hypothesis, import-linter, ruff or mypy in CI, Playwright, frontend component tests, AI eval
harness, GitHub Actions.

## 6. Documentation

`README.md` (setup, Docker Compose, roles, worker, timeline, PostgreSQL, integrations, API, limitations),
`docs/FINAL_QA_REPORT.md` (v1 QA as of `76e9d7b`, historical), `docs/SCHEDULING.md` (cron).
Missing v1 deliverable: a sample sheet template with fabricated invoices (section 9).

## 7. Architectural decisions to preserve

1. **Conditional UPDATEs decide every race** (send reservation, job claim, lease, workflow transitions),
   backed by partial unique indexes. This is portable and already proven by threaded tests.
2. **No transaction open during provider calls.** Validate, then reserve (short transaction), then call,
   then record.
3. **Gmail is never retried, and uncertainty stops the line** (`DELIVERY_UNKNOWN` until a person
   reconciles).
4. **The delivery path lives only in `followups.delivery`.** The outbox registry means `automation` never
   imports it.
5. **Snapshot checks** at approval and send.
6. **Human workflow steps are derived from rows on every read**, so a refresh or restart resumes.
7. **The timeline has a single writer and is append-only in the database.**
8. **Deterministic rules in code, AI only for words** (and, in v2, labels that the backend re-validates).

## 8. Decisions that differ from the v2 document

| v2 says | Repository | Status |
| --- | --- | --- |
| PostgreSQL 16 | PostgreSQL 16 (M08.1) | Aligned |
| Docker Compose (db, api, worker, web) | `docker-compose.yml` with those four services (M08.1) | Aligned |
| `SELECT … FOR UPDATE SKIP LOCKED` | Outbox claiming uses it (M08.1) | Aligned |
| Postgres trigger for the timeline | PL/pgSQL trigger blocking UPDATE, DELETE and TRUNCATE (M08.1) | Aligned |
| `run_scheduler` process | Not built yet | To be folded into `run_worker` (Phase 02 plan) |

## 9. Incomplete v1 requirements found

| v1 requirement | Finding | Severity |
| --- | --- | --- |
| Dashboard "latest sync and stale-data indicator" (§10) | The latest sync is shown (`SyncStatus.tsx`), but nothing warns when data is old | Low; v2 defines it (stale after 26 h), so it is planned in Phase 02 |
| Invoice table "search/filter" (§10) | Status filter tabs only; no text search; `GET /api/invoices/` has no query filters | Low; planned in Phase 05 (`/invoices`) |
| `GET /api/invoices/{id}/` "invoice and follow-up history" (§8) | Returns the invoice only; the UI fetches `follow-ups/?invoice=` separately | Low; functionally covered; Phase 05 adds the detail payload |
| "Sample sheet template and test data" deliverable (§13) | Not in the repository | Medium for submission; planned in Phase 08 (earlier if wanted) |
| Recorded presentation | Outside the repository | Not assessable here |

Removed invoices still count in dashboard totals (noted in the v1 QA report). v1 does not require
excluding them, so this is a known limitation rather than a gap; Phase 06 analytics will exclude them.
