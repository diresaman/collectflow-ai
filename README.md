<img src="frontend/src/app/icon.svg" alt="" width="56">

# CollectFlow AI

An AI-powered accounts receivable follow-up assistant for small service businesses.

CollectFlow AI reads your invoices from a Google Sheet, finds the overdue ones, drafts a payment
reminder for each with Gemini, and sends each reminder through Gmail only after a person has
approved it and confirmed that specific send.

> **Status:** feature-complete MVP (Milestones 01–05.5), final QA in
> [docs/FINAL_QA_REPORT.md](docs/FINAL_QA_REPORT.md). Built for local, single-operator use.

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
              ├── pages: /  (dashboard)   /automation  (workspace)
              └── /api/backend/*  server-side proxy: route allowlist, same-origin check,
                                  adds Authorization: Bearer <operator token>
                     │
                     ▼
            Django REST API (localhost:8000)  ── every endpoint except /api/health/ requires the token
              ├── apps/invoices    sync service ──▶ Google Sheets API (service account, read-only)
              ├── apps/followups   generation   ──▶ Gemini API (google-genai)
              │                    approval workflow, delivery ──▶ Gmail API (OAuth, gmail.send only)
              ├── apps/automation  orchestrator, policy, lease, workflow view
              └── SQLite           invoices, follow-ups, runs, attempts, lease

 cron ──▶ manage.py run_automation  (same orchestrator; never sends email)
```

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
| Backend | Python 3.10+, Django 5.2, Django REST Framework 3.15+, django-cors-headers, SQLite |
| Integrations | `google-api-python-client` (Sheets v4, Gmail v1), `google-auth`, `google-auth-oauthlib`, `google-genai` (Gemini) |
| Frontend | Next.js 16 (App Router, Turbopack), React 19, TypeScript 5, Tailwind CSS 4, ESLint 9 |
| Scheduling | cron (or any scheduler) calling `manage.py run_automation` |

## Quick start (local)

```bash
# 1. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set DJANGO_DEBUG=true and COLLECTFLOW_OPERATOR_TOKEN (see below)
python manage.py migrate
python manage.py runserver      # http://localhost:8000 (binds to 127.0.0.1)

# 2. Frontend (second terminal)
cd frontend
npm install
cp .env.example .env.local      # COLLECTFLOW_OPERATOR_TOKEN must equal the backend value
npm run dev                     # http://localhost:3000 (binds to localhost only)
```

Then configure the integrations: [Google Sheets](#google-sheets-setup), [Gemini](#ai-follow-ups-gemini)
and [Gmail](#gmail-delivery). Generate the operator token with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## Repository layout

```
collectflow-ai/
├── backend/                    Django + Django REST Framework API
│   ├── config/                 Project configuration (settings, root URLs, WSGI/ASGI)
│   ├── apps/core/              Health check and operator-token authentication
│   ├── apps/invoices/          Invoice domain
│   │   ├── models.py           Invoice, AutomationRun
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
│   │   ├── workflow.py         Interactive workflow state (derived from persisted data)
│   │   └── models.py           AutomationRunItem, AutomationLease, WorkflowStep
│   ├── integrations/gemini.py  Gemini API adapter (google-genai)
│   ├── integrations/gmail.py   Gmail send-only adapter (OAuth 2.0 user credentials)
│   ├── credentials/            Service account key (gitignored; create locally)
│   ├── requirements.txt
│   └── .env.example
└── frontend/                   Next.js (App Router) + TypeScript + Tailwind CSS
    ├── src/app/                Dashboard (/), automation workspace (/automation), /api/backend proxy
    ├── src/components/         ApiStatus and dashboard components
    ├── src/lib/api/            Typed API client
    └── .env.example
```

## Prerequisites

- Python 3.10+ (3.11+ recommended; Google's client libraries warn on 3.10)
- Node.js 20.9+ and npm
- A Google Cloud service account with the Google Sheets API enabled

## Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the values (see below)
python manage.py migrate
python manage.py runserver    # http://localhost:8000
```

Check it: `curl http://localhost:8000/api/health/` → `{"status": "ok", "service": "collectflow-api"}`

### Backend environment variables

| Variable                         | Default                  | Purpose                                                        |
| -------------------------------- | ------------------------ | -------------------------------------------------------------- |
| `DJANGO_DEBUG`                   | `false`                  | Enable debug mode (use `true` locally).                        |
| `DJANGO_SECRET_KEY`              | dev fallback when debug  | **Required** when `DJANGO_DEBUG=false`.                        |
| `DJANGO_ALLOWED_HOSTS`           | `localhost,127.0.0.1`    | Comma-separated hostnames.                                     |
| `CORS_ALLOWED_ORIGINS`           | `http://localhost:3000`  | Comma-separated frontend origins allowed by CORS.              |
| `SQLITE_PATH`                    | `db.sqlite3`             | SQLite file, relative to `backend/`.                           |
| `DJANGO_TIME_ZONE`               | `UTC`                    | Defines "today" for overdue calculations.                      |
| `COLLECTFLOW_OPERATOR_TOKEN`     | — (required)             | Shared secret for business endpoints. Empty = all rejected.    |
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

| Days overdue | Tone           |
| ------------ | -------------- |
| 1–7          | `FRIENDLY`     |
| 8–14         | `PROFESSIONAL` |
| 15+          | `FIRM`         |

### Generation workflow

1. **Analyze with AI** (`POST /api/invoices/{id}/analyze/`) checks eligibility and refuses if the
   invoice already has an active (draft or approved) reminder.
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
5. Every attempt is recorded in `GenerationAttempt` (outcome and a safe error message only).

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
2. `POST /api/follow-ups/{id}/send/` re-validates everything before touching Gmail:
   - the follow-up is `APPROVED`;
   - the invoice is still unpaid and overdue, and its snapshot fields are unchanged;
   - the recipient is valid and allowed;
   - subject and body are within the limits and contain no links;
   - Gmail is authorized for the configured sender.
3. **Reservation:** a single conditional `UPDATE … SET status='SENDING' WHERE status='APPROVED'`
   plus a `DeliveryAttempt` row, committed before Gmail is called. Only one request can win the
   update, on any database, and a partial unique index allows only one `SENDING` attempt per
   reminder.
4. Gmail `users.messages.send` is called with no transaction open and **no retries**. The
   exact approved subject and body are sent as a plain-text MIME message, base64url-encoded.
5. The result is recorded with conditional updates.

| Outcome | Follow-up / attempt | HTTP | Meaning |
| --- | --- | --- | --- |
| Gmail returned a message ID | `SENT` (+ message/thread ID, `sent_at`) | `200` `code: sent` | Gmail accepted the email. |
| Gmail answered 4xx, or the connection could not be opened | `FAILED` | `502` `code: delivery_failed` | Reliable evidence it was **not** sent. |
| Timeout, dropped connection, 5xx, response without ID, crash | `DELIVERY_UNKNOWN` | `202` `code: delivery_unknown`, `requires_reconciliation: true` | It **may** have been sent. Do not resend. |
| Not ready / ineligible / duplicate / invalid, before reservation | unchanged | `503` / `409` / `400` | Nothing was submitted. |

`202` is used for unknown outcomes on purpose: a `5xx` would invite clients to retry.

### Duplicate-send protection

- Only `APPROVED` reminders can be sent. `SENDING`, `SENT` and `DELIVERY_UNKNOWN` return `409
  duplicate_delivery`.
- The reservation is an atomic conditional update (it does not rely on `select_for_update`,
  which SQLite ignores), backed by a unique index on in-flight attempts.
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

## Proactive automation

### Architecture

`AutomationOrchestrator` (`apps/automation/orchestrator.py`) reuses the existing services and
adds no new business logic of its own:

1. **Lease:** acquire the single automation lease, or return a conflict.
2. **Sync:** `sync_invoices()` (Milestone 02) records its own `SYNC` run. If it fails, the
   automation run is `FAILED` and **no invoice is evaluated**. Drafts are never generated from a
   failed sync.
3. **Evaluate:** every invoice is checked against the policy below, and one
   `AutomationRunItem` is recorded per invoice (`GENERATED` / `SKIPPED` / `FAILED` plus a
   `reason_code`).
4. **Draft:** for eligible invoices, `FollowUpGenerationService.generate()` (Milestone 03) is
   called, up to `AUTOMATION_MAX_DRAFTS_PER_RUN`. A Gemini failure is recorded on that invoice's
   item and the run continues.
5. **Finish:** the run ends `COMPLETED` (no failures), `PARTIAL` (some drafts failed, some
   succeeded) or `FAILED` (sync failed, every draft failed, or the run was interrupted).

The orchestrator **never calls Gmail**. Every generated draft lands in the dashboard's
**Approval queue** ("Drafts generated — awaiting approval") and still needs approval plus an
explicit, confirmed send. No transaction is held during Google Sheets or Gemini calls, and
Gemini requests are bounded (30s timeout, short retry window).

### Eligibility policy (deterministic)

An invoice gets a new draft only if none of these apply; the first match is the item's
`reason_code`:

| Reason code | Rule |
| --- | --- |
| `stale_draft` / `stale_approved` | An active draft/approval exists but the invoice changed since generation. It is surfaced for review, never replaced or sent. |
| `draft_awaiting_review` / `approved_awaiting_send` | An active draft or approved reminder already exists. |
| `delivery_unresolved` | A delivery is `SENDING` or `DELIVERY_UNKNOWN` (reconcile it first). |
| `not_in_sheet` / `not_unpaid` / `not_overdue` / `invalid_recipient` / `invalid_amount` | Invoice rules shared with manual generation (in the latest sync, unpaid, due **before** today, valid email, amount > 0). |
| `delivery_failed` | The most recent reminder's delivery `FAILED`; operator attention needed (draft a new one manually). |
| `cooldown` | A reminder was confirmed `SENT` less than `FOLLOWUP_COOLDOWN_DAYS` (default 7) ago. |
| `recently_rejected` | The operator rejected the most recent draft within the cooldown period. |
| `run_limit_reached` | The run already generated `AUTOMATION_MAX_DRAFTS_PER_RUN` drafts. |

Failures use the generation error codes (`ai_unavailable`, `ai_bad_response`,
`stale_invoice`, `unexpected_error`). Items store the invoice ID and codes only: no customer
names or emails.

### Running it

- **Dashboard:** **Run Automation** (`POST /api/automation/run/`, trigger `MANUAL`). The panel
  polls while a run is active and shows counts and per-invoice results.
- **Command line:** `python manage.py run_automation` (trigger `SCHEDULED`). It prints a summary
  with counts and reason codes. Exit codes: `0` completed/partial, `1` fatal failure, `2` another
  run in progress.
- **Dry run:** `python manage.py run_automation --dry-run` applies the policy to the current
  database and prints what would be drafted. No sync, no Gemini, no Gmail, no writes.

### Scheduling with cron (Linux)

The command loads `backend/.env` itself (via its absolute path), so cron only needs absolute
paths. Example: weekdays at 08:00 with a log file (adjust `/home/dires/projects/collectflow-ai`
to your checkout):

```bash
mkdir -p /home/dires/projects/collectflow-ai/backend/logs
crontab -e
```

```cron
# CollectFlow AI: draft reminders for overdue invoices (never sends email)
0 8 * * 1-5 cd /home/dires/projects/collectflow-ai/backend && /home/dires/projects/collectflow-ai/backend/.venv/bin/python /home/dires/projects/collectflow-ai/backend/manage.py run_automation >> /home/dires/projects/collectflow-ai/backend/logs/automation.log 2>&1
```

- Environment: `backend/.env` is read automatically. To use a different file, export the
  variables in the cron line (e.g. `GEMINI_MODEL=... /path/to/python ...`).
- Test exactly what cron will run, with cron's minimal environment:
  `env -i HOME=$HOME /bin/sh -c '<the command above, without >> …>'`.
- Check with `crontab -l` and `tail -f backend/logs/automation.log`. Nothing is scheduled
  until you add the crontab entry. The command only exists so cron can call it.

### Concurrency and duplicate protection

- **One run at a time:** `AutomationLease` is a single database row acquired with a conditional
  `UPDATE … WHERE owner='' OR expires_at < now`, which is atomic on SQLite. Manual and scheduled
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

**SQLite limitations:** SQLite serializes writes and ignores `select_for_update`. Correctness
relies on conditional updates and unique constraints, not row locks. A long write elsewhere can
briefly delay others ("database is locked" after Django's default 5s timeout). For multiple
workers or hosts, use PostgreSQL.

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
cp .env.example .env.local    # set COLLECTFLOW_OPERATOR_TOKEN to the backend value
npm run dev                   # http://localhost:3000
```

`npm run dev` and `npm run start` bind to `localhost` only. The `/api/backend` proxy attaches
the operator token for anyone who can reach the Next.js server, so don't expose it on a network
(e.g. `-H 0.0.0.0`) without adding real user authentication first.

### Frontend environment variables

| Variable                     | Example                 | Exposed to browser | Purpose                                     |
| ---------------------------- | ----------------------- | ------------------ | ------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL`   | `http://localhost:8000` | Yes                | Django URL for the public health check.     |
| `API_BASE_URL`               | `http://localhost:8000` | No                 | Django URL used by the server-side proxy.   |
| `COLLECTFLOW_OPERATOR_TOKEN` | same as backend         | No                 | Sent by the proxy to Django.                |

`NEXT_PUBLIC_*` variables are embedded in the browser bundle at build time, so never put
secrets in them. Business data requests go from the browser to the same-origin route
`/api/backend/...`, which adds the operator token on the server and forwards them to Django.

If you run the frontend on a port other than 3000, add that origin to
`CORS_ALLOWED_ORIGINS` in `backend/.env` (needed for the health check).

## Running checks

```bash
# Backend (from backend/, with the virtualenv active)
python manage.py check
python manage.py makemigrations --check --dry-run   # models and committed migrations agree
python manage.py test

# Frontend (from frontend/)
npm run lint
npm run typecheck
npm run build
```

Tests mock the Google Sheets, Gemini and Gmail APIs; they never make network calls or send email.
The automation tests replace `GmailClient.send` with a function that fails the test if called,
and a route-wide test asserts that every `/api/` endpoint except the health check returns `401`
without a valid operator token.

**Manual end-to-end check** (uses your real integrations; only allowlisted recipients can be
emailed while `DJANGO_DEBUG=true`):

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

All endpoints except `/api/health/` require `Authorization: Bearer <COLLECTFLOW_OPERATOR_TOKEN>`.

| Method | Path                     | Response                                                                    |
| ------ | ------------------------ | --------------------------------------------------------------------------- |
| GET    | `/api/health/`           | `200 {"status": "ok", "service": "collectflow-api"}` (public)               |
| POST   | `/api/sync/`             | Automation run: `200` succeeded, `422` invalid rows, `502` Google/DB failure |
| GET    | `/api/invoices/`         | List of invoices, including `is_overdue` and `days_overdue`                  |
| GET    | `/api/invoices/{id}/`    | A single invoice (UUID)                                                      |
| GET    | `/api/dashboard/`        | Counts, plus outstanding and overdue amounts grouped by currency             |
| GET    | `/api/automation-runs/`  | The 20 most recent runs, newest first. Filter: `?kind=SYNC` or `?kind=AUTOMATION` |
| GET    | `/api/automation-runs/{id}/` | One run; automation runs include per-invoice `items`                      |
| POST   | `/api/automation/run/`   | Run the automation (sync + drafts, never sends). `200` completed/partial · `502` failed (body has the run) · `409 automation_running` |
| GET/POST | `/api/automation/workflows/…` | Interactive workflow endpoints (see "Interactive automation workspace") |
| POST   | `/api/invoices/{id}/analyze/` | Generate an AI draft. Body: `{"regenerate": false}`. `201` draft; `409` not eligible / active reminder exists / invoice changed; `502` invalid AI output; `503` Gemini unavailable |
| GET    | `/api/follow-ups/`       | Follow-ups, newest first. Filters: `?invoice=<id>`, `?status=DRAFT`              |
| GET    | `/api/follow-ups/{id}/`  | A single follow-up                                                               |
| PATCH  | `/api/follow-ups/{id}/`  | Edit `subject` / `body` of a draft. `400` invalid content; `409` not a draft      |
| POST   | `/api/follow-ups/{id}/approve/` | `DRAFT → APPROVED` (no email is sent). `409` wrong status or stale invoice |
| POST   | `/api/follow-ups/{id}/reject/`  | `DRAFT → REJECTED`, or withdraw an unsent approval (`APPROVED → REJECTED`). `409` wrong status |
| POST   | `/api/follow-ups/{id}/send/`    | Send an approved reminder via Gmail. `200` sent · `202` delivery unknown (reconcile, do not retry) · `502` Gmail refused · `409` wrong state/stale/duplicate · `503` Gmail not ready |
| GET    | `/api/follow-ups/{id}/delivery-attempts/` | Delivery attempts, newest first                                  |
| GET    | `/api/integrations/gmail/status/` | Whether Gmail is configured, authorized and matches the sender           |

Errors return `{"detail": "...", "code": "..."}`; `401` means a missing or invalid operator token.

## Known limitations

Consolidated from the sections above. See also [docs/FINAL_QA_REPORT.md](docs/FINAL_QA_REPORT.md).

- **Local, single-operator MVP.** There are no user accounts. A shared operator token protects
  Django, and the Next.js proxy adds it for any caller that can reach the frontend, so the
  frontend must stay on `localhost`. A multi-user deployment needs real authentication,
  PostgreSQL and HTTPS.
- **Synchronous work in requests.** Sync, Gemini drafting, Gmail sends and manual automation
  runs execute inside HTTP requests (bounded by timeouts). There is no task queue.
- **SQLite.** Correctness relies on conditional updates and unique constraints, not row locks.
  Use PostgreSQL for multiple workers.
- **Invoices removed from the sheet** stay in the local database and in dashboard totals. They
  are only blocked from reminders (`not_in_sheet`).
- **AI content rules.** Links (`http(s)://`, `www.`), length, single-line subject and tone are
  enforced in code. Everything else (no invented fees, deadlines or payment claims, no bare
  domains such as `pay.example.com`) relies on the model's instructions plus mandatory human
  review before approval.
- **Gmail.** Plain text only, one sender account, no reply tracking. "Sent" means Gmail accepted
  the message, not that it was delivered. Unknown outcomes are reconciled by CLI only. While the
  OAuth consent screen is in *Testing*, refresh tokens expire after 7 days.
- **Interrupted runs** are shown as processing until their lease expires (up to
  `AUTOMATION_LEASE_SECONDS`, default 10 minutes).
- English-only drafts with a generic "Accounts Receivable Team" sign-off. No pagination on the
  invoice and follow-up lists.
