"""
Django settings for the CollectFlow AI API.

All environment-specific values are read from environment variables. A local
`.env` file (see `.env.example`) is loaded automatically for development.
"""

import os
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
    "apps.invoices",
    "apps.followups",
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("SQLITE_PATH", "db.sqlite3"),
    }
}


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
    # Business endpoints require the operator token; public views opt out explicitly.
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.core.authentication.OperatorTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}


# CORS
# ------------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", default="http://localhost:3000")


# Local operator access
# ------------------------------------------------------------------------------

# Shared secret for the local MVP. The Next.js server proxy sends it; browsers never see it.
OPERATOR_TOKEN = os.environ.get("COLLECTFLOW_OPERATOR_TOKEN", "")


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
