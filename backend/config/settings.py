"""
Django settings for the CollectFlow AI API.

All environment-specific values are read from environment variables. A local
`.env` file (see `.env.example`) is loaded automatically for development.
"""

import os
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# Core
# ------------------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")
    # Development-only fallback so a fresh checkout runs without extra setup.
    SECRET_KEY = "django-insecure-local-development-only"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")


# Application definition
# ------------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    # Local
    "apps.core",
    "apps.accounts",
    "apps.timeline",
    "apps.invoices",
    "apps.followups",
    "apps.sequences",
    "apps.inbox",
    "apps.cases",
    "apps.insights",
    "apps.automation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Must come before CommonMiddleware so CORS headers are added to every response.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# ------------------------------------------------------------------------------

# PostgreSQL 16. Connection details come from the environment (backend/.env, or the Docker
# Compose service environment). Timestamps are stored as timestamptz in UTC; DJANGO_TIME_ZONE
# only decides what "today" means for business rules.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "collectflow"),
        "USER": os.environ.get("POSTGRES_USER", "collectflow"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# Read-only source for the one-time SQLite → PostgreSQL transfer (manage.py migrate_from_sqlite).
# Only defined when SQLITE_SOURCE_PATH is set; the application never writes to it.
if os.environ.get("SQLITE_SOURCE_PATH"):
    DATABASES["sqlite_source"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": f"file:{os.path.abspath(os.environ['SQLITE_SOURCE_PATH'])}?mode=ro",
        "OPTIONS": {"uri": True},
        "TEST": {"MIRROR": "default"},
    }
DATABASE_ROUTERS = ["config.db_routers.SourceDatabaseRouter"]


# Users, passwords and sessions
# ------------------------------------------------------------------------------

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# The browser only talks to the Next.js proxy (same origin), which forwards these cookies.
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", str(12 * 60 * 60)))
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SAMESITE = "Strict"
# The frontend reads the CSRF cookie and echoes it in X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False
# Local development runs over plain HTTP; anything else must use HTTPS.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", default=not DEBUG)

# Failed logins allowed per username within the window before further attempts get 429.
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_FAILURE_WINDOW_SECONDS = int(os.environ.get("LOGIN_FAILURE_WINDOW_SECONDS", str(15 * 60)))

# Django admin is off unless explicitly enabled.
ENABLE_ADMIN = env_bool("ENABLE_ADMIN", default=False)

# Password validation
# ------------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# ------------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
# Determines what "today" means for overdue calculations.
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True


# Static files
# ------------------------------------------------------------------------------

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Django REST Framework
# ------------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # Business endpoints require a session (with CSRF on writes) and at least the OPERATOR
    # role; public views opt out explicitly and stricter views declare a higher role.
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.accounts.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.permissions.IsOperator"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
}


# CORS
# ------------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", default="http://localhost:3000")


# Google Sheets
# ------------------------------------------------------------------------------

GOOGLE_SHEETS_SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID", "")
GOOGLE_SHEETS_RANGE = os.environ.get("GOOGLE_SHEETS_RANGE", "Sheet1!A1:G")

# Path to a service account JSON key; relative paths resolve from the backend directory.
_credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
GOOGLE_APPLICATION_CREDENTIALS = str(BASE_DIR / _credentials_path) if _credentials_path else ""


# Gemini (AI follow-up drafts)
# ------------------------------------------------------------------------------

# Server-side only; never exposed to the frontend.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


# Gmail (sending approved reminders)
# ------------------------------------------------------------------------------

def _backend_path(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    return str(BASE_DIR / value) if value else ""


# OAuth client (Desktop app) downloaded from Google Cloud; used only by `authorize_gmail`.
GMAIL_OAUTH_CLIENT_FILE = _backend_path("GMAIL_OAUTH_CLIENT_FILE", "credentials/gmail-oauth-client.json")
# Authorized user token written by `authorize_gmail` (gitignored, mode 0600).
GMAIL_TOKEN_FILE = _backend_path("GMAIL_TOKEN_FILE", "credentials/gmail-token.json")
GMAIL_SENDER_EMAIL = os.environ.get("GMAIL_SENDER_EMAIL", "").strip()
# Recipients that may be emailed: addresses or @domains. Required while DEBUG is on so
# development can only email addresses the developer controls. Empty in production = no limit.
GMAIL_ALLOWED_RECIPIENTS = [item.lower() for item in env_list("GMAIL_ALLOWED_RECIPIENTS")]


# Proactive automation
# ------------------------------------------------------------------------------

# Days after a confirmed SENT (or operator-rejected) reminder before another is drafted.
FOLLOWUP_COOLDOWN_DAYS = int(os.environ.get("FOLLOWUP_COOLDOWN_DAYS", "7"))
# Upper bound on Gemini drafts per run (bounds runtime and cost).
AUTOMATION_MAX_DRAFTS_PER_RUN = int(os.environ.get("AUTOMATION_MAX_DRAFTS_PER_RUN", "10"))
# Lease duration; renewed after every step. Must exceed the slowest single step (~2 minutes).
AUTOMATION_LEASE_SECONDS = int(os.environ.get("AUTOMATION_LEASE_SECONDS", "600"))


# Background worker (outbox jobs)
# ------------------------------------------------------------------------------

# Seconds between queue polls when the worker is idle.
WORKER_POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1"))
# A RUNNING job whose worker has not finished it within this time is considered abandoned.
JOB_LOCK_TIMEOUT_SECONDS = int(os.environ.get("JOB_LOCK_TIMEOUT_SECONDS", "900"))
# Default attempts for retryable jobs (delivery jobs always use 1).
JOB_MAX_ATTEMPTS = int(os.environ.get("JOB_MAX_ATTEMPTS", "3"))
# First retry delay; doubles on every further attempt.
JOB_RETRY_BASE_SECONDS = int(os.environ.get("JOB_RETRY_BASE_SECONDS", "30"))


# Sequences and approval
# ------------------------------------------------------------------------------

# Reminders for invoices at or above this amount (in the invoice's own currency) need an APPROVER
# who is not the last editor (four-eyes rule).
HIGH_VALUE_THRESHOLD = Decimal(os.environ.get("HIGH_VALUE_THRESHOLD", "5000"))
# Minutes between scheduled sequence ticks (enqueued by run_worker).
SEQUENCE_TICK_MINUTES = int(os.environ.get("SEQUENCE_TICK_MINUTES", "15"))
# A draft still "being generated" after this long is treated as abandoned and retried.
SEQUENCE_DRAFTING_TIMEOUT_SECONDS = int(os.environ.get("SEQUENCE_DRAFTING_TIMEOUT_SECONDS", "900"))
# Hours without a successful sync before the dashboard flags the data as stale.
STALE_DATA_HOURS = int(os.environ.get("STALE_DATA_HOURS", "26"))

# Gemini spending guard for automatic drafting (M09.1). Manual drafts are never blocked, but count.
AI_DAILY_GENERATION_BUDGET = int(os.environ.get("AI_DAILY_GENERATION_BUDGET", "15"))
# After a quota error (HTTP 429) all automatic drafting waits this long, doubling per consecutive error.
AI_QUOTA_BACKOFF_MINUTES = int(os.environ.get("AI_QUOTA_BACKOFF_MINUTES", "30"))
# After a transient Gemini failure a sequence step is retried after this long, doubling per failure.
AI_RETRY_BASE_MINUTES = int(os.environ.get("AI_RETRY_BASE_MINUTES", "15"))
AI_RETRY_MAX_HOURS = int(os.environ.get("AI_RETRY_MAX_HOURS", "24"))


# Reply ingestion and classification (M10)
# ------------------------------------------------------------------------------

# Reading replies needs the gmail.readonly scope, granted with `authorize_gmail --with-read-access`.
# Off until the owner explicitly approves read access: no poll is scheduled and no mailbox is read.
GMAIL_READ_ENABLED = env_bool("GMAIL_READ_ENABLED", False)
# Minutes between scheduled reply polls (enqueued by run_worker).
INBOX_POLL_MINUTES = int(os.environ.get("INBOX_POLL_MINUTES", "5"))
# On first start, and when Gmail's history cursor has expired, threads CollectFlow sent to in this
# many days are scanned directly (duplicates are ignored).
INBOX_RESCAN_DAYS = int(os.environ.get("INBOX_RESCAN_DAYS", "30"))
# Stored reply text is capped (after quotes and signatures are stripped) and purged after this long.
INBOX_BODY_MAX_CHARS = int(os.environ.get("INBOX_BODY_MAX_CHARS", "8000"))
INBOX_BODY_RETENTION_DAYS = int(os.environ.get("INBOX_BODY_RETENTION_DAYS", "90"))
# AI labels at or above this confidence are applied; anything lower goes to human triage.
REPLY_CONFIDENCE_THRESHOLD = float(os.environ.get("REPLY_CONFIDENCE_THRESHOLD", "0.8"))
# Automatic classification attempts per reply after transient Gemini failures (then triage only).
INBOX_CLASSIFY_MAX_ATTEMPTS = int(os.environ.get("INBOX_CLASSIFY_MAX_ATTEMPTS", "3"))
# Times an attempt may wait out a Gemini quota pause without counting as an attempt (M14 fix).
INBOX_CLASSIFY_MAX_DEFERRALS = int(os.environ.get("INBOX_CLASSIFY_MAX_DEFERRALS", "6"))
# Out-of-office: the next step waits until the return date (+1 day), at most this many days ahead;
# without a return date it waits the default.
OUT_OF_OFFICE_MAX_DAYS = int(os.environ.get("OUT_OF_OFFICE_MAX_DAYS", "21"))
OUT_OF_OFFICE_DEFAULT_DAYS = int(os.environ.get("OUT_OF_OFFICE_DEFAULT_DAYS", "7"))


# Promises, disputes and tasks (M11)
# ------------------------------------------------------------------------------

# A promise is kept if the sheet shows the invoice PAID by the promised date plus this many days.
PROMISE_GRACE_DAYS = int(os.environ.get("PROMISE_GRACE_DAYS", "2"))
# The latest promised date a person may confirm, in days from today (later ones stay in triage).
PROMISE_MAX_DAYS = int(os.environ.get("PROMISE_MAX_DAYS", "60"))
# Minutes between scheduled promise and overdue-task checks (enqueued by run_worker).
CASES_MONITOR_MINUTES = int(os.environ.get("CASES_MONITOR_MINUTES", "60"))
# Days until a new task from a reply or dispute is due.
TASK_DUE_DAYS = int(os.environ.get("TASK_DUE_DAYS", "2"))

# Client risk and worklist (M13)
# ------------------------------------------------------------------------------
# Factor maxima are from the v2 design; the mappings below were proposed in M13 and approved by the owner.

# Days overdue (oldest unpaid invoice) that earn the full 35 points; linear below.
RISK_OVERDUE_FULL_DAYS = int(os.environ.get("RISK_OVERDUE_FULL_DAYS", "90"))
# Broken promises count within this many days (1 broken -> 15 points, 2 or more -> 25).
RISK_BROKEN_PROMISE_WINDOW_DAYS = int(os.environ.get("RISK_BROKEN_PROMISE_WINDOW_DAYS", "180"))
# Average days late that earns the full 20 points; linear below.
RISK_LATE_FULL_DAYS = int(os.environ.get("RISK_LATE_FULL_DAYS", "60"))
# Below this many measured payments a client's average days late uses the portfolio average.
RISK_MIN_PAID_HISTORY = int(os.environ.get("RISK_MIN_PAID_HISTORY", "3"))
# A send counts as unanswered only after this many days without a reply.
RISK_REPLY_WAIT_DAYS = int(os.environ.get("RISK_REPLY_WAIT_DAYS", "3"))
# Consolidated statements (M14): the least number of chaseable invoices a statement may list.
STATEMENT_MIN_INVOICES = int(os.environ.get("STATEMENT_MIN_INVOICES", "2"))
# Analytics and forecast (M14). Only the aging buckets and the forecast rule are from the design; the
# values below were proposed in M14 (docs/v2/10-m14-implementation-report.md, section 3).
# DSO and CEI period, in days.
ANALYTICS_PERIOD_DAYS = int(os.environ.get("ANALYTICS_PERIOD_DAYS", "30"))
# Attribution: payments seen in the last LOOKBACK days; "after a reminder" if within WINDOW days of one.
ATTRIBUTION_LOOKBACK_DAYS = int(os.environ.get("ATTRIBUTION_LOOKBACK_DAYS", "90"))
ATTRIBUTION_WINDOW_DAYS = int(os.environ.get("ATTRIBUTION_WINDOW_DAYS", "7"))
FORECAST_DAYS = 30
# Fewer settled promises or measured payments than this: portfolio averages, low confidence (design).
FORECAST_MIN_HISTORY = 3
# P(paid within 30 days) by aging bucket, before adjustments.
FORECAST_BUCKET_P = {"current": "0.80", "1_30": "0.60", "31_60": "0.40", "61_90": "0.25", "90_plus": "0.10"}
FORECAST_BEYOND_FACTOR = "0.25"
FORECAST_BROKEN_PROMISE_FACTOR = "0.5"
FORECAST_PROMISE_DEFAULT = "0.5"
# M15 proposal awaiting owner approval (off = {}): days ahead to place an already-overdue invoice whose
# expected date has passed, by aging bucket, e.g. {"1_30": 7, "31_60": 14, "61_90": 21, "90_plus": 28}.
FORECAST_OVERDUE_LAG_DAYS: dict[str, int] = {}
# Minutes between scheduled recomputes (time moves scores even when nothing changes).
INSIGHTS_REFRESH_MINUTES = int(os.environ.get("INSIGHTS_REFRESH_MINUTES", "60"))
