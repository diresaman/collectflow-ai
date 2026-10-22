"""
Gmail read adapter (M10): reads replies in conversations CollectFlow started. It never sends,
modifies or deletes mail, and it is separate from `integrations.gmail` (send) so read code has no
path to delivery.

It needs the `gmail.readonly` scope on the sender account's token, granted only by running
`authorize_gmail --with-read-access` after the owner approves read access. Without it every call
raises MailboxNotAuthorized. A refreshed access token is kept in memory only; the shared token file
is never rewritten from here.

Errors carry messages that are safe to show to operators (no tokens, no message contents):
- MailboxNotReady / MailboxNotAuthorized: configuration or authorization is missing.
- MailboxUnavailable: Google could not be reached or refused for a transient reason (try next poll).
- HistoryExpired: the stored history cursor is too old; the caller rescans known threads.
"""

import json
import logging
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httplib2
from django.conf import settings
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp, Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TIMEOUT_SECONDS = 20
HISTORY_PAGE_SIZE = 500


class MailboxError(Exception):
    code = "mailbox_error"


class MailboxNotReady(MailboxError):
    code = "read_not_configured"


class MailboxNotAuthorized(MailboxError):
    code = "read_not_authorized"


class MailboxUnavailable(MailboxError):
    code = "mailbox_unavailable"


class HistoryExpired(MailboxError):
    code = "history_expired"


@dataclass(frozen=True)
class MessageRef:
    message_id: str
    thread_id: str
    label_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryPage:
    messages: list[MessageRef]
    next_page_token: str
    history_id: str


@dataclass(frozen=True)
class MailMessage:
    message_id: str
    thread_id: str
    label_ids: tuple[str, ...]
    internal_date_ms: int
    headers: dict[str, str]  # lower-cased names; the first value of each header
    payload: dict[str, Any] = field(repr=False)


def parse_message(data: dict[str, Any]) -> MailMessage:
    payload = data.get("payload") or {}
    headers: dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = str(header.get("name", "")).lower()
        if name and name not in headers:
            headers[name] = str(header.get("value", ""))
    return MailMessage(
        message_id=str(data.get("id", "")),
        thread_id=str(data.get("threadId", "")),
        label_ids=tuple(data.get("labelIds") or ()),
        internal_date_ms=int(data.get("internalDate") or 0),
        headers=headers,
        payload=payload,
    )


class MailboxReader:
    def __init__(self, *, token_file: str, mailbox_email: str) -> None:
        self.token_file = Path(token_file) if token_file else None
        self.mailbox_email = mailbox_email.strip().lower()
        self._service: Any = None

    @classmethod
    def from_settings(cls) -> "MailboxReader":
        return cls(token_file=settings.GMAIL_TOKEN_FILE, mailbox_email=settings.GMAIL_SENDER_EMAIL)

    # Readiness ------------------------------------------------------------------

    def check_configured(self) -> tuple[bool, str]:
        """Local-only (no network): read access enabled, mailbox and token file configured."""
        if not settings.GMAIL_READ_ENABLED:
            return False, "Reading replies is not enabled (GMAIL_READ_ENABLED is off)."
        if not self.mailbox_email:
            return False, "GMAIL_SENDER_EMAIL is not configured."
        if self.token_file is None or not self.token_file.is_file():
            return False, "Gmail is not authorized. Run: python manage.py authorize_gmail --with-read-access"
        return True, "Configured."

    def _connect(self) -> Any:
        if self._service is not None:
            return self._service
        ready, detail = self.check_configured()
        if not ready:
            raise MailboxNotReady(detail)
        try:
            # Requesting only the read scope: Google refuses the refresh if it was never granted.
            credentials = Credentials.from_authorized_user_file(str(self.token_file), [READ_SCOPE])
        except (ValueError, KeyError, json.JSONDecodeError, OSError):
            raise MailboxNotReady("The Gmail token file is invalid. Run: python manage.py authorize_gmail") from None
        if not credentials.refresh_token:
            raise MailboxNotAuthorized("The Gmail token has no refresh token. Re-run authorize_gmail --with-read-access.")
        try:
            credentials.refresh(Request(httplib2.Http(timeout=TIMEOUT_SECONDS)))
        except RefreshError:
            logger.warning("Gmail read token refresh was refused")
            raise MailboxNotAuthorized(
                "Gmail read access is not granted (or was revoked). After approval, run: "
                "python manage.py authorize_gmail --with-read-access"
            ) from None
        except (GoogleAuthError, httplib2.HttpLib2Error, OSError):
            logger.warning("Gmail read token refresh failed (network)")
            raise MailboxUnavailable("Could not reach Google to refresh Gmail authorization.") from None
        http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=TIMEOUT_SECONDS))
        try:
            self._service = build("gmail", "v1", http=http, cache_discovery=False)
        except (httplib2.HttpLib2Error, OSError, HttpError):
            raise MailboxUnavailable("Could not initialise the Gmail API client.") from None
        return self._service

    def _execute(self, request: Any, *, what: str, expired_on_404: bool = False) -> dict[str, Any]:
        try:
            response = request.execute(num_retries=0)
        except HttpError as exc:
            status = int(exc.resp.status) if exc.resp is not None else 0
            logger.warning("Gmail %s returned HTTP %s", what, status)
            if status == 404 and expired_on_404:
                raise HistoryExpired("The Gmail history cursor has expired.") from None
            if status in (401, 403):
                raise MailboxNotAuthorized(f"Gmail refused {what} (HTTP {status}). Check read authorization.") from None
            raise MailboxUnavailable(f"Gmail {what} failed (HTTP {status}).") from None
        except (TimeoutError, socket.timeout, httplib2.HttpLib2Error, OSError):
            logger.warning("Gmail %s was interrupted (network)", what)
            raise MailboxUnavailable(f"Could not reach Gmail ({what}).") from None
        return response if isinstance(response, dict) else {}

    def profile(self) -> tuple[str, str]:
        """(mailbox email, current historyId). Confirms the token belongs to the sender mailbox."""
        service = self._connect()
        data = self._execute(service.users().getProfile(userId="me"), what="profile")
        email = str(data.get("emailAddress", "")).lower()
        if email != self.mailbox_email:
            raise MailboxNotAuthorized(
                f"The authorized mailbox ({email or 'unknown'}) is not GMAIL_SENDER_EMAIL ({self.mailbox_email})."
            )
        return email, str(data.get("historyId", ""))

    # Reading ----------------------------------------------------------------------

    def history_page(self, start_history_id: str, page_token: str = "") -> HistoryPage:
        """Messages added since `start_history_id`, one page at a time."""
        service = self._connect()
        kwargs: dict[str, Any] = {
            "userId": "me", "startHistoryId": start_history_id, "historyTypes": ["messageAdded"],
            "maxResults": HISTORY_PAGE_SIZE,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        data = self._execute(service.users().history().list(**kwargs), what="history", expired_on_404=True)
        refs: list[MessageRef] = []
        for record in data.get("history") or []:
            for added in record.get("messagesAdded") or []:
                message = added.get("message") or {}
                if message.get("id") and message.get("threadId"):
                    refs.append(MessageRef(str(message["id"]), str(message["threadId"]),
                                           tuple(message.get("labelIds") or ())))
        return HistoryPage(refs, str(data.get("nextPageToken") or ""), str(data.get("historyId") or ""))

    def thread_messages(self, thread_id: str) -> list[MessageRef]:
        """Message IDs in a thread (no content). A thread that no longer exists has none."""
        service = self._connect()
        try:
            data = self._execute(service.users().threads().get(userId="me", id=thread_id, format="minimal"),
                                 what="thread", expired_on_404=True)
        except HistoryExpired:
            return []  # 404: the thread was deleted
        return [MessageRef(str(m.get("id")), str(m.get("threadId") or thread_id), tuple(m.get("labelIds") or ()))
                for m in data.get("messages") or [] if m.get("id")]

    def get_message(self, message_id: str) -> MailMessage | None:
        """The full message, or None if it no longer exists."""
        service = self._connect()
        try:
            data = self._execute(service.users().messages().get(userId="me", id=message_id, format="full"),
                                 what="message", expired_on_404=True)
        except HistoryExpired:
            return None
        return parse_message(data)
