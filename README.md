# CollectFlow AI

An AI-powered accounts receivable follow-up assistant for small service businesses.

> **Current status — Milestone 02 (Google Sheets invoice sync):** invoices are read from a
> Google Sheet into Django and shown on a Next.js dashboard. AI follow-ups (Gemini) and
> sending (Gmail) are not implemented yet.

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

Tests mock the Google API; they never call Google.

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
