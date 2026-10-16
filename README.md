# CollectFlow AI

An AI-powered accounts receivable follow-up assistant for small service businesses.

> **Current status — Milestone 01 (Project Foundation):** a Django REST API and a Next.js
> frontend talking to each other via a health check. Invoice sync (Google Sheets), AI
> follow-ups (Gemini) and sending (Gmail) are not implemented yet.

## Repository layout

```
collectflow-ai/
├── backend/                 Django + Django REST Framework API
│   ├── config/              Project configuration (settings, root URLs, WSGI/ASGI)
│   ├── apps/core/           Health-check endpoint and its tests
│   ├── requirements.txt
│   └── .env.example
└── frontend/                Next.js (App Router) + TypeScript + Tailwind CSS
    ├── src/app/             Pages and layout
    ├── src/components/      UI components (ApiStatus)
    ├── src/lib/api/         Typed API client
    └── .env.example
```

## Prerequisites

- Python 3.10+
- Node.js 20.9+ and npm

## Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # adjust values if needed
python manage.py migrate
python manage.py runserver    # http://localhost:8000
```

Check it: `curl http://localhost:8000/api/health/` → `{"status": "ok", "service": "collectflow-api"}`

### Backend environment variables

| Variable               | Default                  | Purpose                                              |
| ---------------------- | ------------------------ | ---------------------------------------------------- |
| `DJANGO_DEBUG`         | `false`                  | Enable debug mode (use `true` locally).              |
| `DJANGO_SECRET_KEY`    | dev fallback when debug  | **Required** when `DJANGO_DEBUG=false`.              |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1`    | Comma-separated hostnames.                           |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000`  | Comma-separated frontend origins allowed by CORS.    |
| `SQLITE_PATH`          | `db.sqlite3`             | SQLite file, relative to `backend/`.                 |

## Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local    # adjust values if needed
npm run dev                   # http://localhost:3000
```

### Frontend environment variables

| Variable                   | Example                 | Purpose                                 |
| -------------------------- | ----------------------- | --------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Base URL of the Django API (no slash).  |

`NEXT_PUBLIC_*` variables are embedded in the browser bundle at build time. Never put
secrets in frontend environment files; secrets belong in `backend/.env` only.

If you run the frontend on a port other than 3000, add that origin to
`CORS_ALLOWED_ORIGINS` in `backend/.env`.

## Running checks

```bash
# Backend (from backend/, with the virtualenv active)
python manage.py check
python manage.py test

# Frontend (from frontend/)
npm run lint
npm run typecheck
npm run build
```

## API

| Method | Path           | Auth | Response                                         |
| ------ | -------------- | ---- | ------------------------------------------------ |
| GET    | `/api/health/` | None | `200 {"status": "ok", "service": "collectflow-api"}` |
