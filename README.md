# CollectFlow AI

An AI-powered accounts receivable follow-up assistant for small service businesses.

> **Current status — Milestone 03 (Gemini AI follow-ups):** invoices sync from a Google Sheet,
> and Gemini drafts payment reminders for overdue invoices that an operator edits, approves or
> rejects. Email delivery (Gmail) is not implemented yet: approving a reminder does **not** send it.

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
│   │   └── views.py            REST endpoints
│   ├── integrations/gemini.py  Gemini API adapter (google-genai)
│   ├── credentials/            Service account key (gitignored; create locally)
│   ├── requirements.txt
│   └── .env.example
└── frontend/                   Next.js (App Router) + TypeScript + Tailwind CSS
    ├── src/app/                Page, layout, and /api/backend proxy route
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
updated, and invoices missing from the sheet are kept. Empty rows are skipped. If **any**
nonempty row is invalid (or two rows share an `invoice_id`), the whole batch is rejected, no
invoices change, and the errors are reported with sheet row numbers. Every attempt is
recorded as an automation run.

## AI follow-ups (Gemini)

### Setup

1. Create an API key at [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) in `backend/.env`, then restart Django.
   The key stays on the backend; the browser only talks to the Next.js proxy.

### Business rules (deterministic, never decided by the AI)

An invoice is **eligible** for a reminder only if it is `UNPAID`, its due date is **before
today** (due today is not overdue), its email is valid and its amount is positive. "Today"
follows `DJANGO_TIME_ZONE`.

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
DRAFT ──approve──▶ APPROVED      (SENT / FAILED reserved for email delivery, Milestone 04)
  │
  └──reject───▶ REJECTED
```

- Only `DRAFT` follow-ups can be edited (subject ≤ 150 chars, single line; body 20–5000 chars).
- **Approve** re-reads the invoice. It refuses (HTTP 409) if the invoice is no longer eligible
  (e.g. now paid) or if any snapshot field changed since generation (`external_id`,
  `client_name`, `client_email`, `amount`, `currency`, `due_date`, `status`).
- Approval only records the decision; **it does not send any email**.
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

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local    # set COLLECTFLOW_OPERATOR_TOKEN to the backend value
npm run dev                   # http://localhost:3000
```

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
python manage.py migrate
python manage.py test

# Frontend (from frontend/)
npm run lint
npm run typecheck
npm run build
```

Tests mock the Google Sheets and Gemini APIs; they never make network calls.

## API

All endpoints except `/api/health/` require `Authorization: Bearer <COLLECTFLOW_OPERATOR_TOKEN>`.

| Method | Path                     | Response                                                                    |
| ------ | ------------------------ | --------------------------------------------------------------------------- |
| GET    | `/api/health/`           | `200 {"status": "ok", "service": "collectflow-api"}` (public)               |
| POST   | `/api/sync/`             | Automation run: `200` succeeded, `422` invalid rows, `502` Google/DB failure |
| GET    | `/api/invoices/`         | List of invoices, including `is_overdue` and `days_overdue`                  |
| GET    | `/api/invoices/{id}/`    | A single invoice (UUID)                                                      |
| GET    | `/api/dashboard/`        | Counts, plus outstanding and overdue amounts grouped by currency             |
| GET    | `/api/automation-runs/`  | The 20 most recent sync runs, newest first                                   |
| POST   | `/api/invoices/{id}/analyze/` | Generate an AI draft. Body: `{"regenerate": false}`. `201` draft; `409` not eligible / active reminder exists / invoice changed; `502` invalid AI output; `503` Gemini unavailable |
| GET    | `/api/follow-ups/`       | Follow-ups, newest first. Filters: `?invoice=<id>`, `?status=DRAFT`              |
| GET    | `/api/follow-ups/{id}/`  | A single follow-up                                                               |
| PATCH  | `/api/follow-ups/{id}/`  | Edit `subject` / `body` of a draft. `400` invalid content; `409` not a draft      |
| POST   | `/api/follow-ups/{id}/approve/` | `DRAFT → APPROVED` (no email is sent). `409` wrong status or stale invoice |
| POST   | `/api/follow-ups/{id}/reject/`  | `DRAFT → REJECTED`. `409` wrong status                                     |

Errors return `{"detail": "...", "code": "..."}`; `401` means a missing or invalid operator token.
