<img src="frontend/src/app/icon.svg" alt="" width="56">

# CollectFlow AI

An AI-powered accounts receivable follow-up assistant for small service businesses.

CollectFlow AI reads your invoices from a Google Sheet, finds the overdue ones, drafts a payment
reminder for each with Gemini, and sends each reminder through Gmail only after a person has
approved it and confirmed that specific send.

> **Status:** assignment release.
> - **Built:** v2 milestones M08–M15. That is PostgreSQL, sequences, reply reading and classification,
>   promises, disputes and tasks, roles with four-eyes approval, client risk, statements, analytics and the
>   forecast, and final QA.
> - **Live acceptance:** passed on real Sheets, Gmail and Gemini (M12.1).
> - **Production:** runs M13, M14 and the UX work; the M15 fixes are pending deployment.
> - **Release report:** [docs/v2/12-m15-release-readiness-report.md](docs/v2/12-m15-release-readiness-report.md).
> - **Use:** local Docker Compose deployment for a small team.

**Submission documents:**

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture, safety guarantees, AI integration |
| [docs/ANALYTICS.md](docs/ANALYTICS.md) | How to read DSO, CEI and the forecast, and their limits |
| [docs/TESTING.md](docs/TESTING.md) | Testing summary |
| [docs/DEMO.md](docs/DEMO.md) | 15-minute demonstration walkthrough, with a safe demo stack |
| [docs/v2/](docs/v2/) | The design, the roadmap and a report for each milestone |

[docs/FINAL_QA_REPORT.md](docs/FINAL_QA_REPORT.md) is the historical v1 (SQLite) QA report.

> **Google Sheets is the source of truth for invoice payment status.** CollectFlow never writes
> to the sheet and never marks an invoice paid by itself. To record a payment, set the row's
> `status` to `PAID` in the sheet and sync. An invoice whose row disappears from the sheet is no
> longer eligible for reminders (reason `not_in_sheet`).

## Problem

Small service businesses lose cash flow to invoices that slip past their due date. Chasing them
is repetitive, awkward and easy to postpone. Fully automatic dunning tools fix that, but they
send email without a human looking at it, which is risky when the invoice data lives in a
hand-edited spreadsheet.

CollectFlow automates the tedious parts (spotting overdue invoices, picking the right tone,
writing the email) and keeps a person in control of every message that leaves the building.

## Features

- **Google Sheets sync:** read-only, all-or-nothing batches with row-level validation errors.
- **Overdue detection:** deterministic eligibility rules and a tone policy based on days
  overdue (friendly / professional / firm). These rules are never delegated to the AI.
- **Gemini drafts:** structured JSON output with the tone pinned by the backend. The output is
  validated (lengths, tone, no links), and spreadsheet data is treated as untrusted.
- **Human approval:** drafts can be edited, approved, rejected or regenerated. Approving never
  sends anything.
- **Confirmed Gmail delivery:** one reminder at a time with a confirmation dialog, duplicate-send
  protection, no automatic retries, and an explicit "delivery unknown" state with manual
  reconciliation.
- **Proactive automation:** `run_automation` (cron) or **Run Automation** (UI) syncs, evaluates
  and drafts, with a cooldown policy and a single-run lease. It never sends email.
- **Interactive workspace (`/automation`):** persisted step-by-step progress that survives a
  browser refresh, plus review, send confirmation and a final summary.
- **Audit trail:** sync runs, automation runs with per-invoice reason codes, generation attempts
  and delivery attempts (Gmail message IDs, sanitized errors).

## Architecture

```
 Browser ──▶ Next.js (localhost:3000)
              ├── pages: /login, then one mounted app shell: /today /inbox /approvals /tasks /disputes
              │          /clients / (invoices) /analytics /sequences /automation /settings/integrations
              └── /api/backend/*  server-side proxy: route allowlist, same-origin check,
                                  forwards only the session + CSRF cookies and X-CSRFToken
                     │
                     ▼
            Django REST API (localhost:8000)  ── session login; every endpoint except health/login
              │                                  needs a role (OPERATOR < APPROVER < ADMIN) and CSRF on writes
              ├── apps/accounts    users, roles, login rate limiting
              ├── apps/timeline    append-only TimelineEvent (triggers block UPDATE/DELETE/TRUNCATE)
              ├── apps/invoices    Client, Invoice, sync service
              ├── apps/followups   drafts, statements, approval workflow, delivery (the only Gmail send path)
              ├── apps/sequences   collection sequences (drafts only, never sends)
              ├── apps/inbox       reply reading, AI classification, deterministic triage, holds
              ├── apps/cases       promises to pay, disputes, tasks
              ├── apps/insights    risk scores, worklist, analytics and forecast snapshots
              ├── apps/automation  outbox jobs + worker, orchestrator, policy, lease, workflow view
              └── PostgreSQL 16    state, timeline and the job queue
                     ▲
 manage.py run_worker ┘  claims OutboxJobs ──▶ Google Sheets (read-only) · Gemini · Gmail send
 cron ──▶ manage.py run_automation  (same orchestrator; never sends email)
```

- **No provider calls in web requests.** Sync, AI drafting, Gmail delivery and manual
  automation runs are written as an `OutboxJob` in the same transaction as the state change
  and answered with `202` and a job ID. The `run_worker` process claims jobs with
  `SELECT … FOR UPDATE SKIP LOCKED` and the frontend polls `GET /api/jobs/{id}/` every 1.5 s.

- **Automated phases** (sync → evaluate → draft) run in `AutomationOrchestrator` under a
  database lease and persist each step as a `WorkflowStep`.
- **Human phases** (approve → confirm send → delivery) are *derived* from follow-up and
  delivery-attempt rows on every read. Nothing waits in memory, so a refresh or a restart
  resumes from the database.
- **Follow-up states:** `DRAFT → APPROVED → SENDING → SENT | FAILED | DELIVERY_UNKNOWN`, plus
  `REJECTED`. Transitions are conditional updates, and partial unique constraints allow one
  active reminder per invoice and one in-flight send per reminder.

## Technology stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.12, Django 5.2, Django REST Framework 3.15+, django-cors-headers, argon2-cffi, PostgreSQL 16 (psycopg 3) |
| Integrations | `google-api-python-client` (Sheets v4, Gmail v1), `google-auth`, `google-auth-oauthlib`, `google-genai` (Gemini) |
| Frontend | Next.js 16 (App Router, Turbopack), React 19, TypeScript 5, Tailwind CSS 4, ESLint 9 |
| Jobs | PostgreSQL transactional outbox (`SKIP LOCKED`) processed by `run_worker` (no Redis) |
| Runtime | Docker Compose: `db` (postgres:16), `api`, `worker`, `web` |
| Scheduling | cron (or any scheduler) calling `manage.py run_automation` |

## Quick start (Docker Compose)

```bash
cp backend/.env.example backend/.env       # set DJANGO_DEBUG=true and POSTGRES_PASSWORD (see below)
docker compose up -d --build               # db, api (runs migrate), worker, web
docker compose exec api python manage.py create_user ana --role ADMIN   # prompts for a password
```

Open http://localhost:3000 and sign in. Every port is published on `127.0.0.1` only. Override
the host ports with `WEB_HOST_PORT`, `API_HOST_PORT` and `POSTGRES_HOST_PORT` (default 5433, so
it doesn't clash with a system PostgreSQL on 5432). Useful commands:

```bash
docker compose logs -f worker              # job progress
docker compose exec api python manage.py reconcile_delivery
docker compose down                        # stop (data stays in the pgdata volume)
```

Then configure the integrations: [Google Sheets](#google-sheets-setup), [Gemini](#ai-follow-ups-gemini)
and [Gmail](#gmail-delivery). Credentials go in `backend/credentials/`, which is mounted into the
`api` and `worker` containers. `authorize_gmail` opens a browser, so run it on the host (below).
Without the worker, requests are accepted and queued, and the UI says the worker is not running.

### Without Docker for the app (host processes, Compose database)

```bash
docker compose up -d db                    # PostgreSQL 16 on 127.0.0.1:5433
cd backend
uv venv --python 3.12 .venv && source .venv/bin/activate   # or: python3.12 -m venv .venv
pip install -r requirements.txt
python manage.py migrate
python manage.py create_user ana --role ADMIN
python manage.py runserver                 # terminal 1, http://localhost:8000
python manage.py run_worker                # terminal 2
cd ../frontend && npm install && cp .env.example .env.local && npm run dev   # terminal 3
```

`backend/.env`'s `POSTGRES_HOST=localhost` and `POSTGRES_PORT=5433` point host processes (and
cron) at the Compose database; inside Compose the `api` and `worker` services use `db:5432`.

## Repository layout

```
collectflow-ai/
├── backend/                    Django + Django REST Framework API
│   ├── config/                 Project configuration (settings, root URLs, WSGI/ASGI)
│   ├── apps/core/              Health check, API error format
│   ├── apps/accounts/          Users, roles (Profile), session login, create_user command
│   ├── apps/timeline/          Append-only TimelineEvent and its writer
│   ├── apps/invoices/          Invoice domain
│   │   ├── models.py           Client, Invoice, AutomationRun
│   │   ├── sheets_client.py    Google Sheets API adapter (read-only)
│   │   ├── sheet_parser.py     Header/row validation → invoice records
│   │   ├── sync.py             Synchronization service
│   │   └── views.py            REST endpoints
│   ├── apps/followups/         AI follow-up domain
│   │   ├── models.py           FollowUp (status machine), GenerationAttempt
│   │   ├── eligibility.py      Deterministic eligibility and tone rules
│   │   ├── prompts.py          Gemini instructions, minimal context, output schema
│   │   ├── services.py         Generation service, edit/approve/reject workflow
│   │   ├── delivery.py         Gmail delivery service and manual reconciliation
│   │   └── views.py            REST endpoints
│   ├── apps/automation/        Proactive automation
│   │   ├── policy.py           Deterministic eligibility / cooldown policy
│   │   ├── lease.py            Database-backed run lease (one run at a time)
│   │   ├── orchestrator.py     AutomationOrchestrator (sync → evaluate → draft)
│   │   ├── outbox.py           Job enqueue/claim/retry; handlers registered by each app
│   │   ├── workflow.py         Interactive workflow state (derived from persisted data)
│   │   └── models.py           AutomationRunItem, AutomationLease, WorkflowStep, OutboxJob
│   ├── apps/sequences/         Collection sequences and steps (M09)
│   ├── apps/inbox/             Reply ingestion, classification, triage, holds (M10)
│   ├── apps/cases/             Promises to pay, disputes, tasks (M11)
│   ├── apps/insights/          Risk, worklist, analytics, forecast (M13, M14)
│   ├── integrations/gemini.py  Gemini API adapter (google-genai)
│   ├── integrations/gmail.py   Gmail send-only adapter (OAuth 2.0 user credentials)
│   ├── integrations/mailbox.py Gmail read-only adapter for replies
│   ├── evals/                  AI evaluation datasets and harness (M15)
│   ├── scripts/e2e_backend.sh  Throwaway backend with fake providers (browser tests, demo)
│   ├── credentials/            Service account key, Gmail OAuth files (gitignored; create locally)
│   ├── requirements.txt, requirements-dev.txt
│   └── .env.example            Every setting, no credentials
├── frontend/                   Next.js (App Router) + TypeScript + Tailwind CSS
│   ├── src/app/(app)/          Pages inside the shared app shell
│   ├── src/app/api/backend/    Server-side proxy to the API
│   ├── src/components/         Screens and layout
│   ├── src/lib/                Typed API client, status labels, deep links
│   ├── e2e/                    Playwright journeys
│   └── .env.example
├── docs/                       Submission documents and docs/v2 milestone reports
├── docker-compose.yml
└── .github/workflows/ci.yml
```

## Prerequisites

- Docker with Compose v2 (runs PostgreSQL 16 and, optionally, the whole app)
- Python 3.12 and Node.js 20.9+ for running the app or tests on the host
- A Google Cloud service account with the Google Sheets API enabled

## Backend setup

```bash
docker compose up -d db       # or any PostgreSQL 16 reachable with the POSTGRES_* settings
cd backend
uv venv --python 3.12 .venv   # or: python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values (see below)
python manage.py migrate
python manage.py create_user <username> --role OPERATOR|APPROVER|ADMIN
python manage.py runserver    # http://localhost:8000
python manage.py run_worker   # separate terminal; Ctrl+C finishes the current job, then stops
```

Check it: `curl http://localhost:8000/api/health/` → `{"status": "ok", "service": "collectflow-api"}`

### Users, roles and sessions

- **Roles:** `OPERATOR` < `APPROVER` < `ADMIN`; each includes the ones before it. A user with
  no role cannot sign in. Superusers count as `ADMIN`. Create or change users with
  `python manage.py create_user <name> --role <ROLE>` (`--password-env VAR` for scripts,
  `--keep-password` to change only the role).
- **Login:** `POST /api/auth/login/` after `GET /api/auth/csrf/`. Passwords are hashed with
  Argon2. Five failed attempts for a username within 15 minutes return `429` until the window
  passes. Sessions last 12 hours; cookies are `HttpOnly` (session) and `SameSite=Strict`.
- **CSRF:** every write needs the `X-CSRFToken` header (the frontend copies it from the
  `csrftoken` cookie). The proxy also rejects cross-origin writes.
- **Errors:** `401 not_authenticated` (sign in), `403` (role or CSRF), body `{code, detail, fields?}`.
- **Admin:** `/admin/` is off unless `ENABLE_ADMIN=true`.

### Background worker and jobs

`python manage.py run_worker` processes queued `OutboxJob`s one at a time (`--once` runs what
is due and exits). Jobs: `invoices.sync`, `followups.generate`, `followups.deliver` and
`automation.run`.

- Each job is written in the same transaction as the change that needs it, with a unique
  idempotency key. Claiming is a conditional `UPDATE … WHERE status='PENDING'`, so two workers
  never run the same job.
- Delivery jobs have `max_attempts=1`: Gmail is never retried. Other jobs retry unexpected
  errors with exponential backoff (`JOB_RETRY_BASE_SECONDS`, doubling) up to `JOB_MAX_ATTEMPTS`.
- A job left `RUNNING` longer than `JOB_LOCK_TIMEOUT_SECONDS` (worker crashed) is re-queued if
  it may retry, or failed as `abandoned`. An abandoned delivery leaves the reminder `SENDING`
  for `reconcile_delivery`; it is never resent automatically.
- The worker writes a heartbeat every 10 s; `GET /api/jobs/{id}/` reports `worker_alive`.

### Timeline

`TimelineEvent` is an append-only history per invoice and client: sync imports and field
changes (so `UNPAID → PAID` is a dated fact), removal from / return to the sheet, drafts,
edits, approvals, withdrawals, send requests and outcomes, and reconciliations. Each event
records the acting user, or `SYSTEM` / `SCHEDULER`. A database trigger rejects `UPDATE`,
`DELETE` and `TRUNCATE` on the table. Read it with `GET /api/timeline/?invoice=<id>&client=<id>` (paginated).

### Database (PostgreSQL 16)

- **Connection:** `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`,
  `POSTGRES_PORT` (driver: psycopg 3). Timestamps are `timestamptz` in UTC; `DJANGO_TIME_ZONE`
  only defines the business date.
- **Concurrency:** jobs are claimed with `SELECT … FOR UPDATE SKIP LOCKED`; approvals and edits
  lock the follow-up row (`select_for_update`); the send reservation, the automation lease and job
  completion are conditional `UPDATE`s that PostgreSQL re-checks after waiting on a concurrent
  writer. Partial unique indexes back up "one active reminder per invoice" and "one in-flight
  send per reminder". No transaction is open during a Google call.
- **Timeline:** a PL/pgSQL trigger rejects `UPDATE`, `DELETE` and `TRUNCATE` on
  `timeline_timelineevent`. (A database superuser can still disable triggers; a separate,
  less-privileged application role is future work.)
- **Backups:**

  ```bash
  docker compose exec db pg_dump -U collectflow -Fc collectflow > backups/collectflow-$(date +%F).dump
  # Restore into an empty database (stop api and worker first):
  docker compose exec -T db pg_restore -U collectflow -d collectflow --clean --if-exists < backups/collectflow-2026-10-22.dump
  ```

- **Moving from the old SQLite database:** see [docs/v2/04-m08-postgresql-migration-report.md](docs/v2/04-m08-postgresql-migration-report.md).
  In short: stop the app and cron, then `backend/scripts/cutover_to_postgres.sh` (backup → migrate
  → dry run) and the same with `--commit`. It copies every row with its primary key, verifies
  each one, and never modifies the SQLite file.

### Backend environment variables

| Variable                         | Default                  | Purpose                                                        |
| -------------------------------- | ------------------------ | -------------------------------------------------------------- |
| `DJANGO_DEBUG`                   | `false`                  | Enable debug mode (use `true` locally).                        |
| `DJANGO_SECRET_KEY`              | dev fallback when debug  | **Required** when `DJANGO_DEBUG=false`.                        |
| `DJANGO_ALLOWED_HOSTS`           | `localhost,127.0.0.1`    | Comma-separated hostnames.                                     |
| `CORS_ALLOWED_ORIGINS`           | `http://localhost:3000`  | Comma-separated frontend origins allowed by CORS.              |
| `POSTGRES_DB` / `POSTGRES_USER`  | `collectflow`            | Database name and user (Compose creates them on first start).  |
| `POSTGRES_PASSWORD`              | — (required)             | Database password.                                             |
| `POSTGRES_HOST` / `POSTGRES_PORT`| `localhost` / `5433`     | For host processes; Compose services use `db:5432`.            |
| `POSTGRES_CONN_MAX_AGE`          | `60`                     | Seconds a connection is reused.                                |
| `DJANGO_TIME_ZONE`               | `UTC`                    | Defines "today" for overdue calculations.                      |
| `SECURE_COOKIES`                 | `true` unless debug      | `Secure` flag on session/CSRF cookies (needs HTTPS).           |
| `SESSION_COOKIE_AGE`             | `43200`                  | Session lifetime in seconds (12 h).                            |
| `LOGIN_MAX_FAILURES`             | `5`                      | Failed logins per username per window before `429`.           |
| `LOGIN_FAILURE_WINDOW_SECONDS`   | `900`                    | Login rate-limit window (15 min).                              |
| `ENABLE_ADMIN`                   | `false`                  | Mount Django admin at `/admin/`.                               |
| `GOOGLE_SHEETS_SPREADSHEET_ID`   | — (required for sync)    | ID from the sheet URL: `/spreadsheets/d/<ID>/edit`.            |
| `GOOGLE_SHEETS_RANGE`            | `Sheet1!A1:G`            | A1 range that includes the header row.                         |
| `GOOGLE_APPLICATION_CREDENTIALS` | — (required for sync)    | Path to the service account JSON key (relative to `backend/`). |
| `GEMINI_API_KEY`                 | — (required for AI)      | Gemini API key. Server-side only; never sent to the frontend.  |
| `GEMINI_MODEL`                   | `gemini-3.5-flash`       | Gemini model used for drafting reminders.                      |
| `GMAIL_OAUTH_CLIENT_FILE`        | `credentials/gmail-oauth-client.json` | OAuth "Desktop app" client JSON (used by `authorize_gmail`). |
| `GMAIL_TOKEN_FILE`               | `credentials/gmail-token.json` | Authorized user token written by `authorize_gmail`.      |
| `GMAIL_SENDER_EMAIL`             | — (required to send)     | Dedicated sender account; must match the authorized account.   |
| `GMAIL_ALLOWED_RECIPIENTS`       | — (required in debug)    | Comma-separated addresses / `@domains` that may be emailed.    |
| `FOLLOWUP_COOLDOWN_DAYS`         | `7`                      | Days after a SENT (or rejected) reminder before a new draft.   |
| `AUTOMATION_MAX_DRAFTS_PER_RUN`  | `10`                     | Maximum Gemini drafts generated per automation run.            |
| `AUTOMATION_LEASE_SECONDS`       | `600`                    | Run lease length; renewed after each step.                     |
| `WORKER_POLL_SECONDS`            | `1`                      | Worker idle poll interval.                                     |
| `JOB_LOCK_TIMEOUT_SECONDS`       | `900`                    | A RUNNING job older than this is treated as abandoned.        |
| `JOB_MAX_ATTEMPTS`               | `3`                      | Attempts for retryable jobs (delivery always 1).               |
| `JOB_RETRY_BASE_SECONDS`         | `30`                     | First retry delay; doubles each attempt.                       |
| `HIGH_VALUE_THRESHOLD`           | `5000`                   | Amount (invoice currency) from which an approver must approve. |
| `SEQUENCE_TICK_MINUTES`          | `15`                     | Interval of the worker's scheduled sequence tick.              |
| `SEQUENCE_DRAFTING_TIMEOUT_SECONDS` | `900`                 | A step "being drafted" longer than this is retried.            |
| `STALE_DATA_HOURS`               | `26`                     | Dashboard warns when the last successful sync is older.        |
| `AI_DAILY_GENERATION_BUDGET`     | `15`                     | Gemini attempts per rolling 24 h after which automatic drafting stops. |
| `AI_QUOTA_BACKOFF_MINUTES`       | `30`                     | Pause of all automatic drafting after a quota error; doubles.  |
| `AI_RETRY_BASE_MINUTES`          | `15`                     | Retry delay for a step after a transient AI failure; doubles.  |
| `AI_RETRY_MAX_HOURS`             | `24`                     | Cap for both backoffs.                                         |
| `GMAIL_READ_ENABLED`             | `false`                  | Poll replies (needs approved `gmail.readonly`, see "Customer replies"). |
| `INBOX_POLL_MINUTES`             | `5`                      | Interval of the worker's reply poll.                           |
| `INBOX_RESCAN_DAYS`              | `30`                     | Threads scanned directly on the first poll or an expired cursor. |
| `INBOX_BODY_MAX_CHARS`           | `8000`                   | Stored reply text cap (after quotes/signatures are removed).   |
| `INBOX_BODY_RETENTION_DAYS`      | `90`                     | Reply text older than this is purged daily.                    |
| `REPLY_CONFIDENCE_THRESHOLD`     | `0.8`                    | AI labels below this go to human triage.                       |
| `INBOX_CLASSIFY_MAX_ATTEMPTS`    | `3`                      | Automatic classification attempts after transient failures.   |
| `OUT_OF_OFFICE_MAX_DAYS`         | `21`                     | Longest out-of-office postponement.                            |
| `OUT_OF_OFFICE_DEFAULT_DAYS`     | `7`                      | Postponement when no return date is given.                     |
| `PROMISE_GRACE_DAYS`             | `2`                      | Days after the promised date a payment still counts as kept.  |
| `PROMISE_MAX_DAYS`               | `60`                     | Latest promised date a person may confirm, from today.         |
| `CASES_MONITOR_MINUTES`          | `60`                     | Interval of the promise and overdue-task check (at most 60).   |
| `TASK_DUE_DAYS`                  | `2`                      | Default due time of new reply, dispute and manual tasks.       |

## Google Sheets setup

1. In Google Cloud, enable the **Google Sheets API** and create a **service account**.
   Download a JSON key and save it as `backend/credentials/google-service-account.json`
   (this directory is gitignored).
2. Open the spreadsheet → **Share** → add the service account's `client_email` (found in the
   JSON key) as a **Viewer**.
3. Make the first row of the range the header, with these columns (any order, case-insensitive;
   extra columns are ignored):

   | invoice_id | client_name | client_email | amount | currency | due_date | status |
   | ---------- | ----------- | ------------ | ------ | -------- | -------- | ------ |
   | INV-1001   | Acme Ltd    | ap@acme.com  | 1250   | USD      | 2026-10-01 | UNPAID |

   - `amount`: a positive number with at most two decimals (no currency symbols or thousands separators).
   - `currency`: a three-letter code such as `USD`, `EUR` or `ETB`.
   - `due_date`: a date cell or `YYYY-MM-DD` text.
   - `status`: `UNPAID` or `PAID`.
4. Sync from the dashboard (**Sync Invoices**) or from the command line:

   ```bash
   python manage.py sync_invoices
   ```

**Sync rules:** invoices are matched by `invoice_id`. New ones are created, changed ones are
updated, and invoices missing from the sheet are kept for history. However, they are no longer
eligible for reminders: drafting, approval and sending all refuse an invoice that was not in the
latest successful sync (`not_in_sheet`). Empty rows are skipped. If **any**
nonempty row is invalid (or two rows share an `invoice_id`), the whole batch is rejected, no
invoices change, and the errors are reported with sheet row numbers. Every attempt is
recorded as an automation run.

## AI follow-ups (Gemini)

### Setup

1. Create an API key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) in `backend/.env`, then restart Django.
   The key stays on the backend; the browser only talks to the Next.js proxy.

### Business rules (deterministic, never decided by the AI)

An invoice is **eligible** for a reminder only if it was present in the latest successful
sync, it is `UNPAID`, its due date is **before today** (due today is not overdue), its email is
valid and its amount is positive. "Today" follows `DJANGO_TIME_ZONE`.

The **tone comes from the invoice's collection-sequence step** (see
[Collection sequences](#collection-sequences)), not from the AI and no longer from days overdue:
a never-contacted invoice starts at step 1 (friendly) however overdue it is. Drafts written before
sequences existed keep their stored tone.

### Generation workflow

1. **Analyze with AI** (`POST /api/invoices/{id}/analyze/`) checks eligibility and refuses if the
   invoice already has an active (draft or approved) reminder or a generation is already queued.
   Otherwise it queues a `followups.generate` job and returns `202 {job_id}`; the worker calls
   Gemini, and the job result holds the new `follow_up_id` or the error (`code`, `detail`,
   `http_status`). Generation jobs are not re-run automatically.
2. The backend picks the tone and sends Gemini only the minimum facts: client name, invoice
   number, amount, currency, due date and days overdue. The client email is not sent. Invoice data is
   wrapped in an `<invoice_data>` block that the instructions mark as untrusted data.
3. Gemini must return JSON matching a schema (`subject`, `body`, `tone`, `reasoning`,
   `recommended_action`), with `tone` restricted to the backend's choice. The backend validates
   lengths and types, requires the expected tone and rejects drafts containing links. Invalid output
   is an error (HTTP 502); it is never replaced with fabricated text.
4. No database transaction is open during the Gemini call (30s timeout). The draft is saved in a
   transaction that re-checks the invoice and active reminders, and a partial unique constraint
   guarantees at most one active reminder per invoice.
5. Every attempt is recorded in `GenerationAttempt` (outcome, a safe error message and the
   `prompt_version`), and on the timeline.

The instructions forbid inventing payment links, bank details, penalties or deadlines,
threatening the customer, claiming a payment was verified, or claiming any action was taken.

### Follow-up lifecycle

```
DRAFT ──approve──▶ APPROVED ──send──▶ SENDING ──▶ SENT
  │                  │                    ├──▶ FAILED            (Gmail refused; not sent)
  └──reject───▶ REJECTED ◀──withdraw──┘    └──▶ DELIVERY_UNKNOWN  (may have been sent)

SENDING / DELIVERY_UNKNOWN ──(operator, after checking Gmail)──▶ SENT or FAILED
```

- Only `DRAFT` follow-ups can be edited (subject ≤ 150 chars, single line; body 20–5000 chars).
- **Withdraw approval:** an `APPROVED` reminder that was never sent can be moved to `REJECTED`
  (`POST /reject/`). Its content stays unchanged. Sent, sending or unknown deliveries can't be
  withdrawn, and the change is a conditional update, so it can't overwrite a send that just
  started.
- **Approve** re-reads the invoice. It refuses (HTTP 409) if the invoice is no longer eligible
  (e.g. now paid) or if any snapshot field changed since generation (`external_id`,
  `client_name`, `client_email`, `amount`, `currency`, `due_date`, `status`).
- Approval only records the decision; **it does not send any email**. Sending is a separate,
  confirmed action (see below).
- **Regenerate** must be explicit (`{"regenerate": true}`). It replaces the current draft and marks the old
  one `REJECTED`. An approved reminder cannot be regenerated.

### Known limitations

- Generation is synchronous (the request waits for Gemini, up to ~30s); there is no queue.
- Temporary Gemini overload (HTTP 503/500/429) is retried up to twice within 15s; timeouts
  are not retried. There are no per-user rate limits.
- Google retires models for new keys (e.g. `gemini-2.5-flash` now returns 404 for new users).
  If generation fails with "model not found", pick a current model from the
  [models list](https://ai.google.dev/gemini-api/docs/models) and update `GEMINI_MODEL`.
- The link check is deterministic, but other content rules (e.g. no invented penalties) rely on
  the model following instructions plus human review before approval.
- Drafts are written in English only, and the sign-off is a generic "Accounts Receivable Team".

## Gmail delivery

### OAuth setup (one time)

Use a **dedicated test Gmail account** as the sender.

1. In [Google Cloud Console](https://console.cloud.google.com/) (any project, e.g. the one used
   for Sheets): **APIs & Services → Library → Gmail API → Enable**.
2. **APIs & Services → OAuth consent screen**: choose *External*, fill in the app name and
   emails, and under **Test users** add the sender Gmail account. Leaving the app in *Testing* is
   fine for local use.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app**.
   Download the JSON and save it as `backend/credentials/gmail-oauth-client.json`.
4. In `backend/.env` set `GMAIL_SENDER_EMAIL` to the sender account and
   `GMAIL_ALLOWED_RECIPIENTS` to test addresses you control (e.g. `you@gmail.com,@example.com`).
5. Authorize, in a terminal on the machine running the backend:

   ```bash
   cd backend && source .venv/bin/activate
   python manage.py authorize_gmail          # opens a browser; sign in as GMAIL_SENDER_EMAIL
   python manage.py authorize_gmail --no-browser   # prints the URL instead
   ```

   It requests `https://www.googleapis.com/auth/gmail.send` plus the identity-only scopes
   `openid` and `userinfo.email`. The identity scopes are used only to confirm the authorized
   account matches `GMAIL_SENDER_EMAIL`, because `gmail.send` alone cannot reveal it. **No
   mail-reading scope is requested.**
6. Restart Django. `GET /api/integrations/gmail/status/` (and the dashboard's Send panel) should
   report "Gmail is ready to send."

### Token storage and security

- The OAuth client JSON and the user token live in `backend/credentials/`, which is gitignored.
  The token is written atomically with permissions `600`.
- Expired access tokens are refreshed automatically with the stored refresh token, and the
  refreshed token is saved back to the file. If Google revokes it (password change, app removed,
  or a 7-day expiry while the consent screen is in *Testing*), sending reports "not ready" until you
  run `authorize_gmail` again.
- Tokens are never stored in the database, logged, returned by the API or sent to the browser.
  `DeliveryAttempt` stores only status, Gmail message/thread IDs and sanitized error text.

### Delivery flow

1. **Send Email** is shown only for `APPROVED` reminders. The panel shows From, To (the address
   captured in the approved snapshot), Subject and Body, and asks for confirmation.
2. `POST /api/follow-ups/{id}/send/` validates without calling Google:
   - the follow-up is `APPROVED`;
   - the invoice is still unpaid and overdue, and its snapshot fields are unchanged;
   - the recipient is valid and allowed;
   - subject and body are within the limits and contain no links;
   - Gmail is configured (sender set, token file present).
3. **Reservation:** in one transaction, a conditional `UPDATE … SET status='SENDING' WHERE
   status='APPROVED'`, a `DeliveryAttempt` row (with the confirming user), a timeline event and
   one `followups.deliver` job (`max_attempts=1`). Only one request can win the update, and a
   partial unique index allows only one `SENDING` attempt per reminder. The response is `202`
   with the job and attempt IDs.
4. **Worker:** re-checks the invoice, recipient and content (they may have changed while
   queued) and runs the full Gmail readiness check. If any check fails, nothing was submitted:
   the attempt becomes `FAILED` (`Not sent: …`) and the reminder returns to `APPROVED`.
5. Gmail `users.messages.send` is called once, with no transaction open and **no retries**. The
   exact approved subject and body are sent as a plain-text MIME message, base64url-encoded.
6. The result is recorded with conditional updates and a timeline event.

| Outcome | Follow-up / attempt | Job | Meaning |
| --- | --- | --- | --- |
| Gmail returned a message ID | `SENT` (+ message/thread ID, `sent_at`) | `SUCCEEDED` | Gmail accepted the email. |
| Gmail answered 4xx, or the connection could not be opened | `FAILED` | `SUCCEEDED` | Reliable evidence it was **not** sent. |
| Timeout, dropped connection, 5xx, response without ID, crash | `DELIVERY_UNKNOWN` | `SUCCEEDED` | It **may** have been sent. Do not resend. |
| Gmail not ready / invoice changed while queued | `APPROVED` again / attempt `FAILED` | `FAILED` (`gmail_not_ready`, `stale_invoice`) | Nothing was submitted. |
| Ineligible / duplicate / invalid / not configured, at request time | unchanged | none | `409` / `400` / `503`; nothing was submitted. |

The frontend polls the job, then reads the follow-up and its attempts to show the outcome.

### Duplicate-send protection

- Only `APPROVED` reminders can be sent. `SENDING`, `SENT` and `DELIVERY_UNKNOWN` return `409
  duplicate_delivery`.
- The reservation is an atomic conditional update (it does not rely on `select_for_update`,
  so it holds even without a row lock), backed by a unique index on in-flight attempts.
- Gmail calls never retry, and the UI disables the button while sending. A lost response is
  shown as "outcome unknown" and is never retried.
- While a reminder is `SENDING` or `DELIVERY_UNKNOWN`, no new reminder can be generated for that
  invoice.

### Manual reconciliation

A reminder left in `SENDING` (process crashed mid-send) or `DELIVERY_UNKNOWN` needs a human:

```bash
python manage.py reconcile_delivery                      # list unresolved deliveries
# Check the sender account's Gmail "Sent" folder for the subject, then:
python manage.py reconcile_delivery <follow-up-id> --sent [--message-id <id>]
python manage.py reconcile_delivery <follow-up-id> --failed
```

Nothing is ever resent automatically. After `--failed`, draft a new reminder to try again.

### Known MVP limitations

- Sending is synchronous within the request; there is no queue or scheduler.
- Plain-text emails only; one sender account; no reply tracking or inbox monitoring.
- Reconciliation is a CLI step; there is no UI for it yet.
- With the consent screen in *Testing*, Google expires refresh tokens after 7 days, so you need
  to re-run `authorize_gmail` periodically.
- In production (`DEBUG=false`) an empty `GMAIL_ALLOWED_RECIPIENTS` means any recipient is
  allowed.

## Collection sequences

Each overdue invoice follows a **versioned sequence** of steps. The default ("Standard
collection" v1, seeded by migration):

| Step | Due | Channel | Tone | Approval |
| --- | --- | --- | --- | --- |
| 1 | due date + 1 day | email | friendly | operator |
| 2 | due date + 8 days | email (same Gmail thread) | professional | operator |
| 3 | due date + 15 days | email (same Gmail thread) | firm | **approver** (four-eyes) |
| 4 | due date + 30 days | call task for a person | — | — |

- **Enrollment:** after every successful sync, eligible overdue invoices are enrolled in the
  default sequence (one live enrollment per invoice, enforced by a partial unique index). Paid
  invoices and invoices removed from the sheet leave their sequence (`EXITED`).
- **Progression is deterministic.** An enrollment records the last *completed* step and the
  *pending* step. A step completes only when its reminder is `SENT`, a person `REJECTED` its
  draft, or its call task is done. Creating a draft does not complete a step. `DRAFT`,
  `APPROVED` and `SENDING` wait; `DELIVERY_UNKNOWN` and `FAILED` hold the sequence until a person
  reconciles or re-drafts. Outcomes are recorded the moment they happen (send result, rejection,
  reconciliation), not on the next tick. The next step is due at due date + offset, never earlier than the last
  send + `FOLLOWUP_COOLDOWN_DAYS`. A tick advances an enrollment by **at most one new step**, so a
  machine that was off for days never produces a burst.
- **Existing history** (reminders sent before sequences): the position comes from the reminders
  actually sent. If their tones match the first steps, the invoice continues after them. If not
  (e.g. v1 sent a *professional* first reminder), the enrollment is **flagged for review**. It
  keeps going only if the next step is neither firm nor approver-only; otherwise it is **paused**
  until an approver confirms the position. An ambiguous history never moves an invoice into a
  firm reminder automatically. Historical follow-ups are never modified.
- **Threading:** step 2 onward replies in the Gmail thread of the last sent reminder. CollectFlow
  sets its own `Message-ID` on every send and adds `In-Reply-To`/`References` plus Gmail's
  `threadId`. The subject is fixed to `Re: <original subject>` (Gmail requires it to keep the
  thread), so editing a threaded draft's subject is refused (`400 subject_locked`).
- **Four-eyes approval:** a firm tone, a step marked "approver required", or an amount at or above
  `HIGH_VALUE_THRESHOLD` (default 5,000, in the invoice's currency) needs an **APPROVER who is not
  the reminder's last editor** (`403 approver_required` / `403 self_approval`). The API returns an
  `approval` block on each follow-up so the UI says *why* approval is unavailable.
- **Manual drafting:** "Draft step N" (`POST /api/invoices/{id}/analyze/`) drafts the invoice's
  next step, enrolling it first if needed, even before the step is due. It still refuses a
  pending step, an unknown delivery, a paused sequence or a pending history review.
- **Scheduling:** the worker queues a `sequences.tick` job every 15 minutes
  (`SEQUENCE_TICK_MINUTES`). The idempotency key is the slot start, so restarts and parallel
  workers never queue a slot twice, and missed slots are not back-filled. A successful sync also
  queues a tick. Ticks run under the automation lease (never in parallel with a run or another
  tick), lock each enrollment with `SELECT … FOR UPDATE SKIP LOCKED`, and call Gemini only after
  that transaction commits. **A tick never approves and never sends.**
- **Gemini spending guard (M09.1):** automatic drafting (ticks and runs) stops for the rest of a
  rolling 24 hours once `AI_DAILY_GENERATION_BUDGET` generation attempts (default 15, under the
  free tier's ~20/day) were made, counting manual drafts. After a quota error (HTTP 429) **all**
  automatic drafting pauses for 30 minutes, doubling with each further quota error (capped at
  24 h); a success ends the pause. A transient failure (Gemini busy, timeout) retries that step
  after 15 minutes, doubling per failure. Invalid output or a missing configuration is **not**
  retried automatically: the step waits for a person to draft it by hand. The adapter no longer
  retries 429 within a call. People can always draft manually. The dashboard shows when and why
  automatic drafting is paused.
- **Commands:** `python manage.py tick_sequences [--dry-run]`; `run_automation --dry-run` shows
  the next step per invoice.
- **UI:** the invoice table shows each invoice's step and why it is held. The drawer shows the step
  strip, pause/resume and the history review (approvers). `/sequences` shows versions on a
  day timeline with enrollment counts, and admins can save a new version. Open call tasks appear
  on the dashboard.

## Customer replies (M10)

CollectFlow reads replies **only in Gmail conversations it started** (threads with a SENT
CollectFlow reminder). Nothing else in the mailbox is fetched or stored. It needs the
`gmail.readonly` scope, which is **off until the owner approves it**:

1. Owner approval of read access (it changes what the Google account has granted).
2. `python manage.py authorize_gmail --with-read-access` (same consent flow as sending, plus read).
3. `GMAIL_READ_ENABLED=true` in `backend/.env`, then restart the worker.

Until then no poll is scheduled, and `/settings/integrations` shows *Awaiting authorization*.
Between steps 2 and 3, `python manage.py preview_reply_discovery` shows what the first poll would
store and which invoices it would hold. It reads headers only and changes nothing (see
`docs/v2/06-m10-implementation-report.md` §9.1).

- **Polling:** the worker queues an `inbox.poll` job every `INBOX_POLL_MINUTES` (5). It reads
  `users.history.list` from a stored cursor (`MailboxCursor`), page by page. The cursor moves only
  after every message up to it is stored, so a crash re-reads history and duplicates are ignored
  (Gmail message ID is unique). On the first poll, or when Gmail's history has expired, it scans
  the known threads of the last `INBOX_RESCAN_DAYS` (30) directly. A lease on the cursor row keeps
  polls from overlapping. `python manage.py poll_replies` runs one poll by hand.
- **What is stored:** sender, subject, received time, Gmail IDs, and the reply's own text: plain
  text (HTML converted), attachments ignored, quoted history and signatures removed, capped at
  `INBOX_BODY_MAX_CHARS`. Text older than `INBOX_BODY_RETENTION_DAYS` (90) is purged daily
  (`purge_reply_bodies`). CollectFlow's own messages are skipped. The sender is checked against
  the invoice contact (match / same domain / unknown).
- **A reply stops chasing at once.** Storing it, putting the invoice on a **chase hold** and
  queueing classification happen in one transaction. A hold blocks drafting (re-checked under the
  invoice lock when the draft is saved), approval, send reservation and delivery, so a sequence tick
  that is already running cannot send past a reply.
- **Classification (`classify-v1`):** Gemini returns `{label, confidence, suspicious_instructions,
  rationale, extracted}` against a strict schema; the reply sits in a `<client_reply>` block marked
  as untrusted data. The backend validates it and applies a label **only** when confidence ≥
  `REPLY_CONFIDENCE_THRESHOLD` (0.8), no injection is detected, the sender is known, the reply is
  about this invoice only, and (for labels that resume chasing) the text has no payment or dispute
  wording. Everything else goes to **triage** with the hold kept.

| Label | Chasing | Also |
| --- | --- | --- |
| PROMISE_TO_PAY, DISPUTE | held; always reviewed by a person | (M11 adds promise and dispute records) |
| CLAIMS_PAID | held | `RECONCILE` task. The invoice is never marked paid from an email |
| WRONG_CONTACT | held | `UPDATE_CONTACT` task |
| REQUESTS_INFO | resumes | `SEND_INFO` task |
| OUT_OF_OFFICE | resumes | next step postponed to the return date + 1 day (at most `OUT_OF_OFFICE_MAX_DAYS`, 21; default `OUT_OF_OFFICE_DEFAULT_DAYS`, 7) |
| OTHER | held for review (AI); resumes when a person confirms it | — |

- **Gemini use:** classifications are recorded as `GenerationAttempt(purpose=CLASSIFY)`, so they
  count toward the drafting budget and a quota error pauses drafting too. Classification itself is
  not blocked by the daily drafting budget (it is safety work) but waits out a quota cooldown.
  Transient failures retry automatically at most `INBOX_CLASSIFY_MAX_ATTEMPTS` (3) times in total.
- **Triage (`/inbox`):** the reply, the AI's label, confidence, rationale and extracted fields, why
  it needs review, holds, tasks and the full history. A person picks a label (keys 1–7, j/k to
  move), edits extracted fields and applies it; the same deterministic action runs. **Resuming
  chasing after a promise, dispute or payment claim needs an APPROVER and a note** (enforced in the
  backend: `403 approver_required`). Every AI proposal and every human decision is kept in the
  append-only `ReplyClassification` log (database triggers refuse changes) and on the timeline.
- **Integration health (`/settings/integrations`):** Sheets sync, Gmail send, Gmail read, reply
  polling, Gemini (budget, cooldown) and the worker, plus the scheduled jobs. Built from local
  records only (no provider calls), with no tokens or message text.

## Promises, disputes and tasks (M11)

Customer replies (M10) feed three business workflows. The AI only suggests; people confirm, and the
backend decides every state change. All of them stop chasing through the same **chase holds**: each hold
is owned by one reply, promise or dispute, only its owner ends it, and chasing resumes only when no
hold is left.

- **Promise to pay.** From a PROMISE_TO_PAY reply (Inbox → "Confirm the promise", prefilled with the
  AI's extracted date and amount as suggestions) or from the invoice drawer. Date from today to
  `PROMISE_MAX_DAYS` (60) ahead, amount up to the invoice amount, one OPEN promise per invoice.
  Recording it withdraws unsent reminders (the sequence step stays pending), places a hold and hands
  the reply's hold over to it. The hourly `cases.monitor` job (also run after each sync) decides:
  - **KEPT:** the sheet showed the invoice PAID by promised date + `PROMISE_GRACE_DAYS` (2). The time
    comes from the sync's own record, never from the reply.
  - **BROKEN:** still unpaid after that day. The next reminder is **one tone firmer** (capped at firm)
    and mentions the missed date (prompt `draft-v3`). Other holds still apply, and nothing is approved or sent.
  - **CANCELLED:** an approver withdrew it, with a note.
- **Disputes.** From a DISPUTE reply or the drawer: reason, description and owner. While a case is open,
  every chasing path refuses with `409 invoice_disputed`: drafting, approval, send reservation, a
  delivery already queued, the sequence tick and automation runs. Unsent reminders are withdrawn and an
  approver gets a "Dispute review" task. Operators assign owners and add internal notes. **Only an
  approver resolves**, with a written resolution:
  - **customer is right** (`RESOLVED_VALID`): the sequence exits and the invoice is not re-enrolled until
    the sheet changes it;
  - **invoice stands** (`RESOLVED_INVALID`): the dispute's hold ends.

  The response says whether chasing resumes and, if not, what still blocks it. Nothing is ever sent
  on resolution.
- **Tasks.** Kinds: call (sequence step 4), information request, contact update, reconciliation, dispute
  review and other follow-up. OPEN → DONE (optional note) or CANCELLED (note required). They support
  assignment and due dates, and are audited on the timeline; overdue tasks are flagged once by
  `cases.monitor`. Completing a call task records its sequence step at once, exactly once. Completing any
  other task never resumes chasing. Manual tasks can be added from the drawer.
- **UI:** the invoice drawer's "Promises, disputes and tasks" section lists every hold and who can end
  it. It shows the promise and dispute cards, the record/open forms, approver-only cancel and resolve,
  owner and assignee pickers, notes, tasks and recent history. The Inbox links each reply to the promise
  or dispute made from it.

## Operator workflow (M12)

The screens form one loop: **Invoice → Sequence → Draft → Approval → Delivery → Customer reply →
Classification → Promise, dispute or task → Resolution**. Every screen links to the related record
through deep links: `/invoices?invoice=<id>` opens the invoice drawer (the older `/?invoice=<id>` redirects there), `/inbox?reply=<id>`,
`/disputes?case=<id>` and `/tasks?task=<id>`.

- **Today (`/today`):** what needs a person now, from existing records only, with no scores or
  predictions. Fixed sections in a fixed order, each oldest or most overdue first:
  - integration problems;
  - replies to review;
  - open disputes (unassigned first);
  - unknown delivery outcomes;
  - drafts awaiting approval;
  - approved reminders not sent;
  - broken promises (last 14 days);
  - promised payments due;
  - tasks due today or overdue;
  - sequences that need a person;
  - invoices on hold.

  Each item says why it is there and who can act. If the signed-in user cannot act (e.g. a firm draft
  for an operator), it also says why. The page refreshes every minute.
- **Disputes (`/disputes`):** filter by open, open without an owner, mine, resolved or all. Each case
  shows the invoice, client, amount, due date, the originating reply (with the AI's suggestion), the
  owner, notes and resolution, and whether the invoice is chased now (with every remaining reason if
  not). Operators set owners and add notes; resolving shows the approver requirement instead of
  silently disabling it.
- **Tasks (`/tasks`):** open, mine, done, cancelled or all; open tasks first by due time. Assign, done
  with a note, or cancel with a note (call tasks cannot be cancelled).
- **Invoice drawer:** each hold links to the reply or dispute that owns it; the Inbox links to the
  invoice, the promise or dispute and its tasks.

## Client risk and worklist (M13)

A client is identified by the normalized email of its invoices (v2 design); every client name the
sheet used for it is listed, nothing is merged further. Each client gets a **deterministic 0–100 risk
score** from records only (no AI). The design fixes the maximum per factor; the mappings were approved
by the owner in M13 and are tunable in settings (`RISK_*`):

| Factor | Points |
| --- | --- |
| Oldest unpaid invoice in the sheet, days overdue | 35 × min(days, 90) / 90, rounded |
| Broken promises in the last 180 days | 1 → 15, 2 or more → 25 |
| Average days late on paid invoices (paid date = first sync showing PAID) | 20 × min(avg, 60) / 60; fewer than 3 measured payments → portfolio average, "low confidence" |
| No reply to the last two reminders | 5 per reminder unanswered after 3 days |
| Open dispute | 10 |

Each score is stored on the client with every factor's points, sentence and source records, and the
calculation time. The worker recalculates after any change that writes a relevant timeline event (sync,
payment, reply, send, promise, dispute) and every hour. `POST /api/clients/recompute/` queues one by
hand.

**Clients (`/clients`):** the worklist ranks clients by **risk × outstanding amount, separately per
currency** (never added or compared across currencies). Each row shows the top factors, a suggested
next action taken from the Today rules (with who can act and why the user cannot), and what stops
chasing. Urgent Today work (integration problems, replies to review, unknown deliveries) is shown above
the ranking. The detail panel shows the full breakdown, payment behaviour (average days late, promises
kept, reminders answered) and links to invoices, replies, disputes, promises and tasks. The score never
drafts, approves, sends or releases a hold. APIs: `GET /api/worklist/`, `GET /api/clients/`,
`GET /api/clients/{id}/`.

## Using the app

Start on **Today's Work** (the home page; `/` opens it): it groups everything that needs a person into *Needs
your attention*, *Waiting for approval or sending*, *Blocked or at risk* and *Due now and coming up*, and every item
has one clear action. The sidebar has four groups:

| Group | Menu items (route) |
| --- | --- |
| **Workspace** | Today's Work (`/today`), Customer Replies (`/inbox`), Approvals, Tasks, Disputes. Each shows a live count of what is waiting |
| **Customers & Payments** | Customers (`/clients`), Invoices (`/invoices`) |
| **Reports** | Reports & Insights (`/analytics`) |
| **Settings** | Reminder Schedule (`/sequences`), Automation Settings (`/automation`), Connected Services (`/settings/integrations`) |

The menu names are display wording only. Routes, API endpoints and models keep their names (a customer is a `Client`
in the API), and older invoice links (`/?invoice=<id>`) still open the invoice. **Approvals** lists every reminder
and statement waiting for approval or sending, and who can act. Statuses use the same words everywhere (Awaiting
approval, Approved, not sent, Sent, Delivery unknown, Disputed, Promise due, …); approving never sends, and sending is
always a separate, confirmed step. See `docs/v2/11-ux-simplification-report.md`.

## Statements, analytics and forecast (M14)

- **Consolidated statements:** on a client's panel, "Prepare statement draft" lists every invoice that
  can be chased now (at least two; holds, disputes, in-flight reminders and the cooldown exclude
  invoices, with the reason shown). Gemini writes only the opening and closing; CollectFlow writes the
  invoice lines and per-currency totals, and AI text containing amounts is rejected. The statement is
  reviewed in the drawer of its oldest invoice (and listed under each of its invoices), always needs an
  approver who did not edit it, and is sent through the normal Gmail delivery: every listed invoice is
  checked again at approval, reservation and delivery, and while it is unsent its invoices get no
  separate reminder. A dispute or promise on any of them withdraws it.
- **Analytics (`/analytics`):** summary strip (overdue, expected cash in 30 days, DSO, open promises),
  aging (current, 1–30, 31–60, 61–90, 90+), DSO and CEI over 30 days, payments after reminders, and an
  explained 30-day cash forecast, all per currency. "Billed" means first seen by CollectFlow (the sheet
  has no invoice date) and "paid" means first seen PAID in the sheet, not the bank date; DSO and CEI wait
  for a full tracked period instead of estimating opening balances. The forecast is an estimate:
  disputed invoices and unconfirmed payment claims are never counted. The worker refreshes the figures
  with the risk scores (after each relevant change and hourly); pages only read them. The formulas and
  their proposed parameters are in `docs/v2/10-m14-implementation-report.md`.

## Proactive automation

### Architecture

`AutomationOrchestrator` (`apps/automation/orchestrator.py`) reuses the existing services and
adds no new business logic of its own:

1. **Lease:** acquire the single automation lease, or return a conflict.
2. **Sync:** `sync_invoices()` (Milestone 02) records its own `SYNC` run. If it fails, the
   automation run is `FAILED` and **no invoice is evaluated**. Drafts are never generated from a
   failed sync.
3. **Evaluate:** sequence enrollments are reconciled with the synced invoices (enroll eligible
   overdue invoices; exit paid or removed ones).
4. **Draft:** the sequence tick advances each due enrollment by at most one step (an AI draft or
   a call task), up to `AUTOMATION_MAX_DRAFTS_PER_RUN` drafts. One `AutomationRunItem` is recorded
   per invoice (`GENERATED` / `SKIPPED` / `FAILED` plus a `reason_code`). A Gemini failure leaves
   the step pending for the next tick, and the run continues.
5. **Finish:** the run ends `COMPLETED` (no failures), `PARTIAL` (some drafts failed, some
   succeeded) or `FAILED` (sync failed, every draft failed, or the run was interrupted).

The orchestrator **never calls Gmail**. Every generated draft lands in the dashboard's
**Approval queue** ("Drafts generated — awaiting approval") and still needs approval plus an
explicit, confirmed send. No transaction is held during Google Sheets or Gemini calls, and
Gemini requests are bounded (30s timeout, short retry window).

### Eligibility policy (deterministic)

Each invoice's run item explains what happened to it; the sequence rules above decide. Reason
codes:

| Reason code | Rule |
| --- | --- |
| `stale_draft` / `stale_approved` | An active draft/approval exists but the invoice changed since generation. It is surfaced for review, never replaced or sent. |
| `draft_awaiting_review` / `approved_awaiting_send` | An active draft or approved reminder already exists. |
| `delivery_unresolved` | A delivery is `SENDING` or `DELIVERY_UNKNOWN` (reconcile it first). |
| `not_in_sheet` / `not_unpaid` / `not_overdue` / `invalid_recipient` / `invalid_amount` | Invoice rules shared with manual generation (in the latest sync, unpaid, due **before** today, valid email, amount > 0). |
| `delivery_failed` | The pending step's delivery `FAILED`; draft the step again. |
| `cooldown` / `not_due` | The next step is not due yet (cooldown after the last send, or the step's offset). |
| `recently_rejected` | A pre-sequence draft was rejected within the cooldown period. |
| `history_review` / `sequence_paused` / `sequence_completed` / `sequence_exited` | The invoice's sequence is paused for review, paused by a person, finished, or ended. |
| `task_created` / `task_open` | The call step created its task / the task is still open. |
| `run_limit_reached` | The run already generated `AUTOMATION_MAX_DRAFTS_PER_RUN` drafts. |

Failures use the generation error codes (`ai_unavailable`, `ai_bad_response`,
`stale_invoice`, `unexpected_error`). Items store the invoice ID and codes only: no customer
names or emails.

### Running it

- **Dashboard:** **Run Automation** (`POST /api/automation/run/`, trigger `MANUAL`) queues an
  `automation.run` job (`202`, or `409` while a run is queued or holds the lease); the worker
  runs it. The panel polls while a run is active and shows counts and per-invoice results.
- **Command line:** `python manage.py run_automation` (trigger `SCHEDULED`). It prints a summary
  with counts and reason codes. Exit codes: `0` completed/partial, `1` fatal failure, `2` another
  run in progress.
- **Dry run:** `python manage.py run_automation --dry-run` applies the policy to the current
  database and prints what would be drafted. No sync, no Gemini, no Gmail, no writes.

### Scheduling with cron (Linux)

Unattended runs use the user crontab and the wrapper
[`backend/scripts/scheduled_automation.sh`](backend/scripts/scheduled_automation.sh). The
wrapper `cd`s into `backend/`, runs `.venv/bin/python manage.py run_automation` (trigger
`SCHEDULED`) and appends the output to `backend/logs/automation.log`, between
`=== <timestamp> … started` and `=== <timestamp> finished, exit=<code>` lines. It adds no
logic: scheduled runs go through the same orchestrator, lease, cooldown policy and audit
records as **Run Automation**, and **never send email**. Drafts wait in the approval queue.

**Enable** (daily at 08:00 in the machine's time zone; adjust the path to your checkout):

```bash
chmod +x backend/scripts/scheduled_automation.sh
( crontab -l 2>/dev/null; \
  echo '# collectflow-ai: daily proactive automation (drafts only, never sends email)'; \
  echo '0 8 * * * /home/dires/projects/collectflow-ai/backend/scripts/scheduled_automation.sh' \
) | crontab -
crontab -l | grep -A1 collectflow-ai      # confirm
```

**Check:** `tail -n 30 backend/logs/automation.log`, the dashboard's automation panel, or the
`/automation` workspace (scheduled runs show as "scheduled run"). Exit codes: `0`
completed/partial, `1` failed (e.g. sync error), `2` another run held the lease (nothing done).

**Disable** (removes only the CollectFlow lines; other crontab entries are kept):

```bash
crontab -l | grep -v -e 'collectflow-ai' -e 'scheduled_automation.sh' | crontab -
```

To pause without deleting, comment the line out with `crontab -e`.

Notes:

- `backend/.env` is loaded automatically, and variables set in the cron line take precedence
  (e.g. `POSTGRES_PORT=… /path/to/scheduled_automation.sh`). The database (`docker compose up -d db`)
  must be running at the scheduled time; cron needs no other environment.
- To try exactly what cron runs, in cron's minimal environment:
  `env -i HOME=$HOME /bin/sh -c '/path/to/backend/scripts/scheduled_automation.sh'; echo $?`
- Cron fires in the **system** time zone, while "overdue" uses `DJANGO_TIME_ZONE`. Pick an hour
  at which both agree on the date (08:00 Africa/Addis_Ababa = 05:00 UTC).
- Cron does not catch up: if the machine is off or asleep at 08:00, that day's run is skipped
  and the next one drafts whatever is eligible then. The backend server doesn't need to be
  running; the command talks to the database and the Google APIs directly.
- On Gemini's free tier (about 20 requests/day per model, reset at midnight Pacific time), a run
  before the reset shares the previous day's quota. Its drafts fail with `ai_unavailable` and
  are retried on the next run. Schedule after the reset (e.g. 11:30 EAT) to give it the full
  allowance. See [docs/SCHEDULING.md](docs/SCHEDULING.md#8-known-limitations-of-local-scheduling).
- Each run calls Gemini for at most `AUTOMATION_MAX_DRAFTS_PER_RUN` invoices. Invoices already
  holding a draft, approval or recent reminder are skipped, so a daily schedule does not pile up
  duplicate drafts.

Verification evidence for an unattended, cron-triggered run is in
[docs/SCHEDULING.md](docs/SCHEDULING.md).

### Concurrency and duplicate protection

- **One run at a time:** `AutomationLease` is a single database row acquired with a conditional
  `UPDATE … WHERE owner='' OR expires_at < now`; PostgreSQL re-checks the condition after a
  concurrent update, so only one process wins. Manual and scheduled
  triggers share it. A second trigger gets `409 automation_running`, or exit code `2` from the
  command.
- **Heartbeat:** the owner renews the lease after each step (sync, each invoice). Another run can
  only take over after `AUTOMATION_LEASE_SECONDS` without a heartbeat, i.e. when the owner has
  crashed or stalled. The abandoned run is then marked `FAILED`, and if the stalled owner resumes,
  its renewal fails and it stops.
- **Interrupted runs:** if the server stops mid-run (e.g. Ctrl+C on `runserver`), the next
  workflow read after the lease expires (≤ `AUTOMATION_LEASE_SECONDS`) marks the run `FAILED`
  ("Abandoned"). Its unfinished steps become `FAILED`/`SKIPPED`, so the workspace stops showing
  "Processing". Drafts generated before the interruption remain reviewable.
- **No duplicate drafts:** besides the policy check, generation re-checks under a lock and the
  database's partial unique constraint allows only one active reminder per invoice. Repeated runs
  report `draft_awaiting_review` instead of creating duplicates.


### Known limitations

- A manual run executes inside the HTTP request (up to roughly `max drafts × ~45s`). There is
  no background queue, but the dashboard polls progress, and closing the page does not stop
  the run.
- Runs are bounded by `AUTOMATION_MAX_DRAFTS_PER_RUN`; remaining eligible invoices are drafted
  on the next run.
- A stale **approved** reminder (invoice changed after approval) can't be sent. Withdraw the
  approval, and the next run (or a manual Analyze) drafts a fresh one.
- The dry run evaluates current data without syncing first.

## Interactive automation workspace (`/automation`)

One page for the whole loop: **Run Automation**, live progress, review drafts, confirm each
Gmail send, see delivery results, then **Finish**. It talks only to existing services through
the same server-side proxy.

### Workflow state machine

A workflow is an `AUTOMATION` run (the workflow ID is the run ID). Steps, in order:

| Step | Persisted / derived | Meaning |
| --- | --- | --- |
| `SYNCING_INVOICES` | persisted (`WorkflowStep`) | Google Sheets sync |
| `EVALUATING_INVOICES` | persisted | Policy applied to every invoice (counts eligible / overdue) |
| `GENERATING_DRAFTS` | persisted, detail updated per invoice ("3 of 5 done") | Gemini drafts |
| `WAITING_FOR_APPROVAL` | derived from this run's drafts | `RUNNING` while any draft is still `DRAFT` |
| `WAITING_FOR_SEND_CONFIRMATION` | derived | `RUNNING` while approved reminders are neither sent nor left unsent |
| `SENDING` | derived from follow-ups and delivery attempts | `RUNNING` while a send is in flight; `FAILED` if any delivery failed or is unknown |
| `FINALIZING` | persisted | Set by **Finish** (or automatically when nothing needed review) |

Each step is `PENDING`, `RUNNING`, `COMPLETED`, `FAILED` or `SKIPPED`, with start and finish times
and a short, sanitized detail. The overall state is `PROCESSING` → `WAITING_FOR_APPROVAL` →
`WAITING_FOR_SEND_CONFIRMATION` / `SENDING` → `READY_TO_FINALIZE` → `FINALIZED`.

### How it pauses and resumes

- The orchestrator only does the automated part. When drafting ends it releases the run lease
  and returns. **No request, transaction or lease is held while waiting for a person.**
- The human steps are recomputed from the database on every read. Drafts are followed through
  regeneration, so a regenerated draft stays part of the workflow. Approve, reject, edit,
  regenerate and send use the **existing** follow-up endpoints, unchanged. Refreshing the page
  (or coming back days later) shows the same state and never re-syncs or regenerates.
- `GET /api/automation/workflows/current/` returns the workflow to show: one that is processing,
  otherwise the newest unfinished one, otherwise the newest. The page polls every 1.5s only while
  processing or sending.
- If nothing needs review (no drafts), the run finalizes itself.

### Gmail confirmation

- Approving **never** sends. Approved reminders move to **Confirm Gmail sends**, which shows the
  recipient (from the approved snapshot), subject and full body.
- Each reminder is sent only when you click **Send Email** and confirm the dialog. That calls the
  existing `POST /api/follow-ups/{id}/send/`, with all its eligibility, snapshot, allowlist and
  duplicate-send checks. There is no bulk send.
- **Leave unsent** removes a reminder from this workflow's queue. It stays `APPROVED` and can
  still be sent later from the dashboard. **Finish** leaves any remaining approved reminders
  unsent in the same way.
- "Accepted by Gmail" (`SENT`) means Gmail took the message for delivery, not that the
  recipient received it. `DELIVERY_UNKNOWN` is never counted as sent, never offered for resending,
  and stays visible until reconciled (`manage.py reconcile_delivery`).

### API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/automation/run/` | Start a run (same orchestrator as the command; `409` if one is active) |
| GET | `/api/automation/workflows/current/` | `{"workflow": … \| null}`, used to resume after refresh |
| GET | `/api/automation/workflows/{id}/` | State, current step, steps, summary, pending actions |
| GET | `/api/automation/workflows/{id}/steps/` | Steps only |
| GET | `/api/automation/workflows/{id}/pending-actions/` | Approvals, send confirmations, deliveries, left unsent, attention |
| POST | `/api/automation/workflows/{id}/items/{item_id}/leave-unsent/` | Leave one approved reminder unsent (idempotent) |
| POST | `/api/automation/workflows/{id}/finalize/` | Finish (idempotent; `409` while processing or sending) |

### Workspace limitations

- One open workflow is shown at a time. While it's unfinished, **Run Automation** is disabled
  in the workspace (scheduled runs still start, and appear once they're processing).
- Drafting happens inside the `POST /api/automation/run/` request. Closing the page doesn't stop
  it, and the workspace picks it up again by polling.
- Unreviewed drafts left when you click Finish stay in the dashboard's approval queue.

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev                   # http://localhost:3000 → redirects to /login
```

`npm run dev` and `npm run start` bind to `localhost` only. Every page requires a signed-in
user; an expired session sends the browser back to `/login?next=…`. Before exposing the app on
a network, serve it over HTTPS (session cookies are `Secure` outside debug mode).

### Frontend environment variables

| Variable                     | Example                 | Exposed to browser | Purpose                                     |
| ---------------------------- | ----------------------- | ------------------ | ------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL`   | `http://localhost:8000` | Yes                | Django URL for the public health check.     |
| `API_BASE_URL`               | `http://localhost:8000` | No                 | Django URL used by the server-side proxy.   |

`NEXT_PUBLIC_*` variables are embedded in the browser bundle at build time, so never put
secrets in them. Business data requests go from the browser to the same-origin route
`/api/backend/...`, which forwards only the `sessionid` and `csrftoken` cookies and the
`X-CSRFToken` header to Django and relays the cookies Django sets. The proxy holds no secret.

If you run the frontend on a port other than 3000, add that origin to
`CORS_ALLOWED_ORIGINS` in `backend/.env` (needed for the health check).

## Running checks

```bash
# Backend (from backend/, with the virtualenv active and PostgreSQL running: docker compose up -d db)
python manage.py check
python manage.py makemigrations --check --dry-run   # models and committed migrations agree
python manage.py test --noinput --parallel 1   # one process: it creates and drops test_<POSTGRES_DB>
lint-imports                                   # import contracts (pip install -r requirements-dev.txt)
python manage.py run_ai_evals --mode deterministic   # AI eval datasets and screens, no model calls

# Frontend (from frontend/)
npm run lint
npm run typecheck
npm run build
npm run test:e2e   # Playwright: starts a throwaway backend (collectflow_e2e) with fake Gemini/Gmail
```

Never start a second backend test command while one is running: both use the same `test_<POSTGRES_DB>`
database, and `--noinput` makes the second drop the first one's database. CI (`.github/workflows/ci.yml`)
runs the same steps in separate jobs, each with its own PostgreSQL service, and never uses Google
credentials.

**Browser tests** (`frontend/e2e/`) cover sign-in and navigation, Today, client risk, invoice blockers,
reply review, editing, approving and sending a reminder (fake Gmail), four-eyes approval, disputes,
promises, tasks, analytics, statements (including a stale one), unknown delivery outcomes, integrations,
CSRF, role refusals and the phone layout. `backend/scripts/e2e_backend.sh` recreates the `*_e2e` database,
seeds one fictional record set per journey (`apps/core/e2e.py`) and runs the API and a worker with fake
providers; the e2e commands refuse any database that does not end in `_e2e`.

**AI evaluations** (`backend/evals/`, design §11): 80 labelled replies and 40 drafting scenarios.
`--mode deterministic` checks the datasets and safety screens; `--mode recorded --responses FILE` scores
saved model answers (the bundled `recorded/simulated-*.json` are hand-written and only exercise the harness);
`--mode live --confirm-live --max-requests N` calls Gemini, about 120 requests in total. It needs the owner's
approval, is capped and resumable (cache in `evals/results/`), and stops without using up a case while Gemini
is paused or out of quota.

**Unknown delivery outcome.** A reminder whose Gmail result was lost is `DELIVERY_UNKNOWN` and is never
resent. An admin checks the sender's Sent folder and records the result:
`python manage.py reconcile_delivery` (lists them), then `reconcile_delivery <id> --sent [--message-id …]`
or `--failed`. Only SENDING or DELIVERY_UNKNOWN reminders can be reconciled; the result is written to the
history of every invoice involved.

Tests mock the Google Sheets, Gemini and Gmail APIs; they never make network calls or send email.
The automation tests replace `GmailClient.send` with a function that fails the test if called,
a route-wide test asserts that every `/api/` endpoint except health and login returns `401`
without a session and `403` for a user without a role, and threaded tests with separate PostgreSQL
connections show that workers skip rows locked by others, each job is claimed once, parallel
approvals have one winner and parallel send requests call Gmail at most once. The SQLite → PostgreSQL
transfer is tested against a real SQLite snapshot (`apps/core/tests_sqlite_transfer.py`).

**Manual end-to-end check** (uses your real integrations; only allowlisted recipients can be
emailed while `DJANGO_DEBUG=true`):

0. Start the stack (`docker compose up -d`), then sign in at `/login`.
1. Put a few rows in the sheet, at least one `UNPAID` and past due, with a `client_email` that
   is in `GMAIL_ALLOWED_RECIPIENTS`.
2. Open `/automation` and click **Run Automation**. Watch the steps progress, then refresh the
   page mid-run or at the review step: the same state comes back.
3. Edit and approve one draft and reject another. Nothing is sent.
4. Under **Confirm Gmail sends**, click **Send Email** and confirm. Check the recipient inbox and
   the sender's Sent folder.
5. Click **Finish**. Mark the invoice `PAID` in the sheet, run again, and see it skipped as
   `not_unpaid`.
6. `python manage.py run_automation --dry-run` shows the same policy decisions from the command
   line.

## API

All endpoints except health, CSRF and login/logout require a session from `/api/auth/login/`
and at least the `OPERATOR` role; writes also need `X-CSRFToken`. Endpoints that call a
provider queue a job and return `202`; poll `GET /api/jobs/{id}/`.

| Method | Path                     | Response                                                                    |
| ------ | ------------------------ | --------------------------------------------------------------------------- |
| GET    | `/api/health/`           | `200 {"status": "ok", "service": "collectflow-api"}` (public)               |
| GET    | `/api/auth/csrf/`        | Sets the `csrftoken` cookie (public)                                        |
| POST   | `/api/auth/login/`       | `{"username", "password"}` → current user. `400 invalid_credentials` · `403 no_role` / `csrf_failed` · `429 too_many_attempts` |
| POST   | `/api/auth/logout/`      | Ends the session                                                            |
| GET    | `/api/me/`               | Current user: `username`, `display_name`, `email`, `role`                   |
| GET    | `/api/jobs/{id}/`        | Job `status` (`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`), `result`, `error_code`, `worker_alive` |
| GET    | `/api/timeline/`         | Append-only events, newest first, paginated. Filters: `?invoice=<id>`, `?client=<id>` |
| GET/POST | `/api/sequences/`      | Versions with steps and enrollment counts. POST (ADMIN): `{name, steps, make_default}` creates a new version (2–5 steps) |
| GET    | `/api/sequences/{id}/`   | One version |
| GET    | `/api/enrollments/`      | Filters: `?invoice=`, `?status=`, `?review_required=true`; each with its step states |
| POST   | `/api/enrollments/{id}/pause/`, `/resume/` | Pause with `{reason}` / resume. `409` on the wrong status |
| POST   | `/api/enrollments/{id}/review/` | APPROVER: `{last_completed_step, note}` confirms the position after a history review |
| GET    | `/api/tasks/`            | Tasks; `?open=true`, `?status=`, `?invoice=`, `?assignee=me` |
| POST   | `/api/tasks/{id}/done/`  | `{note?}`: mark done (idempotent); a call task completes its sequence step at once |
| POST   | `/api/sync/`             | `202 {run_id, job_id, run}`; the worker syncs. Read the run for `SUCCEEDED` / `FAILED` with row errors |
| GET    | `/api/invoices/`         | List of invoices, including `is_overdue` and `days_overdue`                  |
| GET    | `/api/invoices/{id}/`    | A single invoice (UUID)                                                      |
| GET    | `/api/dashboard/`        | Counts, plus outstanding and overdue amounts grouped by currency             |
| GET    | `/api/automation-runs/`  | The 20 most recent runs, newest first. Filter: `?kind=SYNC` or `?kind=AUTOMATION` |
| GET    | `/api/automation-runs/{id}/` | One run; automation runs include per-invoice `items`                      |
| POST   | `/api/automation/run/`   | Queue an automation run (sync + drafts, never sends). `202 {job_id}`; the job result has `run_id` · `409 automation_running` |
| GET/POST | `/api/automation/workflows/…` | Interactive workflow endpoints (see "Interactive automation workspace") |
| POST   | `/api/invoices/{id}/analyze/` | Queue an AI draft. Body: `{"regenerate": false}`. `202 {job_id}`; `409` not eligible / active reminder exists / `generation_in_progress`. The job result has `follow_up_id`, or `code` + `http_status` (`502` invalid AI output, `503` Gemini unavailable, `409` invoice changed) |
| GET    | `/api/follow-ups/`       | Follow-ups, newest first. Filters: `?invoice=<id>`, `?status=DRAFT`              |
| GET    | `/api/follow-ups/{id}/`  | A single follow-up                                                               |
| PATCH  | `/api/follow-ups/{id}/`  | Edit `subject` / `body` of a draft. `400` invalid content; `409` not a draft      |
| POST   | `/api/follow-ups/{id}/approve/` | `DRAFT → APPROVED` (no email is sent). `409` wrong status or stale invoice; `403 approver_required` / `403 self_approval` (four-eyes) |
| POST   | `/api/follow-ups/{id}/reject/`  | `DRAFT → REJECTED`, or withdraw an unsent approval (`APPROVED → REJECTED`). `409` wrong status |
| POST   | `/api/follow-ups/{id}/send/`    | Reserve and queue delivery. `202 {job_id, follow_up, delivery_attempt}` · `409` wrong state/stale/duplicate · `400` invalid content · `503` Gmail not configured. The outcome lands on the follow-up and attempt (see Delivery flow) |
| GET    | `/api/follow-ups/{id}/delivery-attempts/` | Delivery attempts, newest first                                  |
| GET    | `/api/integrations/gmail/status/` | Whether Gmail is configured, authorized and matches the sender           |
| GET    | `/api/integrations/status/` | Health of Sheets, Gmail send/read, reply polling, Gemini, worker and scheduled jobs (local records only) |
| GET    | `/api/replies/`          | Customer replies, newest first. `?triage=true`, `?state=ACTIONED`, `?invoice=<id>` |
| GET    | `/api/replies/summary/`  | `{needs_review, invoices_on_hold, by_state}` |
| GET    | `/api/replies/{id}/`     | Reply with text, classifications, holds, tasks, history and a Gmail link |
| POST   | `/api/replies/{id}/classify/` | `{label, extracted?, note?}`: a person classifies or corrects; the label's action runs. `403 approver_required` when it would resume chasing after a promise, dispute or payment claim |
| POST   | `/api/replies/{id}/release-hold/` | `{note}`: resume chasing (APPROVER and a note for protected holds) |
| POST   | `/api/replies/{id}/retry-classification/` | `202 {job_id}`: one more AI attempt for an unclassified reply |
| GET/POST | `/api/promises/`       | `?invoice=`, `?status=`, `?reply=`. POST `{invoice_id, promised_date, amount?, reply_id?, note?}` (operator) → `201`; `400` date or amount out of range; `409 promise_exists` / `invoice_disputed` / `invoice_not_chaseable` |
| POST   | `/api/promises/{id}/cancel/` | APPROVER `{note}`; returns the promise plus `chasing` (resumes, blocked_by) |
| GET/POST | `/api/disputes/`       | `?invoice=`, `?status=` (or `RESOLVED`), `?reply=`, `?owner=me`, `?unassigned=true`; open first. Each case has `chasing` and `can_resolve`. POST `{invoice_id, reason, description, reply_id?, owner_id?}` (operator); `409 dispute_exists` |
| GET    | `/api/disputes/{id}/`    | Case with notes |
| POST   | `/api/disputes/{id}/assign/`, `/notes/` | `{owner_id}` / `{note}` (operator) |
| POST   | `/api/disputes/{id}/resolve/` | APPROVER `{outcome: VALID \| INVALID, resolution}`; returns `chasing` |
| POST   | `/api/tasks/`            | `{invoice_id, title, due_at?, assignee_id?, detail?}`: a manual follow-up task |
| POST   | `/api/tasks/{id}/assign/`, `/cancel/` | `{assignee_id}` / `{note}` (call-step tasks cannot be cancelled) |
| GET    | `/api/users/`            | Active users with a role (id, username, display name, role) for assignment |
| GET    | `/api/today/`            | Actionable work in fixed sections (`key`, `title`, `items` with `title`, `detail`, `since`, `link`, `who_can_act`, `blocked`) |

Errors return `{"code": "...", "detail": "...", "fields"?: {...}}`; `401` means no session, `403` a
missing role or CSRF token.

## Known limitations

Consolidated from the sections above.

- **Release state.** The M15 fixes are not yet deployed. In production, links between screens open the page
  but not the record (finding F4, fixed in M15).
- **AI quality not measured at scale.** The 120-case live evaluation was not run, by the owner's decision for
  the assignment. Evidence is the deterministic and simulated harness runs plus the M12.1 live acceptance test.
  The free Gemini tier (about 20 calls a day) is too small for daily use.
- **Analytics are a demonstration.** Paid dates are when a sync first saw PAID, not bank settlement dates.
  DSO and CEI need a full 30-day history and are unavailable until about 2026-11-15 on the current deployment.
  The forecast uses fixed, unfitted probabilities and is an estimate, not guaranteed collections. Its
  parameters await owner review. See [docs/ANALYTICS.md](docs/ANALYTICS.md).
- **Not built:** payment reconciliation against bank data (M16, out of scope), a screen for unknown delivery
  outcomes (CLI only), and remote CI (no git remote).

- **Local deployment.** Users, roles and sessions are in place, but the app is meant for one
  machine on `localhost`. Exposing it needs HTTPS (cookies are `Secure` outside debug) and a
  production secret store.
- **One worker by default.** Compose runs one `worker`, which processes jobs one at a time.
  Claiming uses `SKIP LOCKED`, so more workers are safe (`docker compose up -d --scale worker=2`).
  Without a worker, requests are accepted and queued, and the UI says so.
- **Local Compose deployment.** Django runs with `runserver` inside the container, ports are bound
  to `127.0.0.1`, and the database role owns its tables (so it could disable the timeline
  trigger). Production would need a WSGI server, HTTPS and a separate least-privilege role.
- **Invoices removed from the sheet** stay in the local database and in dashboard totals. They
  are only blocked from reminders (`not_in_sheet`).
- **AI content rules.** Links (`http(s)://`, `www.`), length, single-line subject and tone are
  enforced in code. Everything else (no invented fees, deadlines or payment claims, no bare
  domains such as `pay.example.com`) relies on the model's instructions plus mandatory human
  review before approval.
- **Gmail.** Plain text only, one sender account. Reply reading (M10) needs the `gmail.readonly`
  scope and `GMAIL_READ_ENABLED=true` (enabled on the current deployment). "Sent" means Gmail accepted
  the message, not that it was delivered. Unknown outcomes are reconciled by CLI only. While the
  OAuth consent screen is in *Testing*, refresh tokens expire after 7 days.
- **Interrupted runs** are shown as processing until their lease expires (up to
  `AUTOMATION_LEASE_SECONDS`, default 10 minutes).
- English-only drafts with a generic "Accounts Receivable Team" sign-off. No pagination on the
  invoice and follow-up lists.
