# Django API and background worker (same image, different command).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as the host user's UID so the mounted credentials/ and logs/ stay writable
# (Gmail token refresh rewrites credentials/gmail-token.json).
ARG UID=1000
RUN useradd --uid "${UID}" --create-home app
COPY --chown=app:app . .
USER app

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
