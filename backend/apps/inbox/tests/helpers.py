import base64
import datetime as dt
from typing import Any

from django.utils import timezone

from apps.followups.models import FollowUp
from apps.invoices.models import Invoice
from apps.sequences.tests.helpers import invoice, sent
from integrations.mailbox import HistoryExpired, HistoryPage, MailboxNotAuthorized, MailMessage, MessageRef

SENDER = "ar@collectflow.test"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def payload(text: str = "", *, html: str = "", attachment: str = "") -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=UTF-8"}],
                      "body": {"data": b64(text)}})
    if html:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    if attachment:
        parts.append({"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": b64(attachment)}})
    return {"mimeType": "multipart/alternative", "parts": parts}


def mail(message_id: str, thread_id: str, *, sender: str, text: str = "Thanks, we will pay on Friday.",
         subject: str = "Re: Reminder", labels: tuple[str, ...] = ("INBOX",), received: dt.datetime | None = None,
         html: str = "") -> MailMessage:
    received = received or timezone.now()
    return MailMessage(
        message_id=message_id, thread_id=thread_id, label_ids=labels,
        internal_date_ms=int(received.timestamp() * 1000),
        headers={"from": f"Client <{sender}>", "subject": subject, "message-id": f"<{message_id}@client.test>",
                 "in-reply-to": "<step1@collectflow.test>"},
        payload=payload(text, html=html),
    )


class FakeMailbox:
    """Stands in for MailboxReader; never touches the network. Records which message IDs were fetched."""

    def __init__(self, *, history_id: str = "100") -> None:
        self.mailbox_email = SENDER
        self.history_id = history_id
        self.messages: dict[str, MailMessage] = {}
        self.pages: list[HistoryPage] = []
        self.threads: dict[str, list[str]] = {}
        self.expired = False
        self.not_authorized = False
        self.fetched: list[str] = []
        self.fail_on_get: str = ""
        self.history_starts: list[str] = []

    def add(self, message: MailMessage, *, in_history: bool = True) -> MailMessage:
        self.messages[message.message_id] = message
        self.threads.setdefault(message.thread_id, []).append(message.message_id)
        if in_history:
            self.pages.append(HistoryPage([MessageRef(message.message_id, message.thread_id, message.label_ids)], "", ""))
        return message

    def profile(self) -> tuple[str, str]:
        if self.not_authorized:
            raise MailboxNotAuthorized("Gmail read access is not granted.")
        return self.mailbox_email, self.history_id

    def history_page(self, start_history_id: str, page_token: str = "") -> HistoryPage:
        self.history_starts.append(start_history_id)
        if self.expired:
            raise HistoryExpired("expired")
        refs = [ref for page in self.pages for ref in page.messages]
        index = int(page_token or 0)
        chunk = refs[index:index + 1]  # one message per page, to exercise pagination
        next_token = str(index + 1) if index + 1 < len(refs) else ""
        return HistoryPage(chunk, next_token, self.history_id)

    def thread_messages(self, thread_id: str) -> list[MessageRef]:
        return [MessageRef(mid, thread_id, self.messages[mid].label_ids) for mid in self.threads.get(thread_id, [])]

    def get_message(self, message_id: str) -> MailMessage | None:
        self.fetched.append(message_id)
        if message_id == self.fail_on_get:
            from integrations.mailbox import MailboxUnavailable

            raise MailboxUnavailable("Gmail message failed (HTTP 503).")
        return self.messages.get(message_id)


def collectflow_thread(external_id: str = "INV-1", thread: str = "thread-1", **fields: Any) -> tuple[Invoice, FollowUp]:
    """An overdue invoice whose step 1 reminder was sent in Gmail thread `thread`."""
    inv = invoice(external_id, **fields)
    return inv, sent(inv, "FRIENDLY", days_ago=3, thread=thread)


def classification(label: str = "PROMISE_TO_PAY", confidence: float = 0.95, **extracted: Any) -> dict[str, Any]:
    fields = {name: "" for name in ("promised_date", "promised_amount", "paid_date", "paid_amount", "payment_reference",
                                    "dispute_reason", "requested_info", "return_date", "new_contact_email")}
    fields["invoice_numbers"] = []
    fields.update(extracted)
    return {"label": label, "confidence": confidence, "suspicious_instructions": False,
            "rationale": "The customer says when they will pay.", "extracted": fields}


class FakeClassifier:
    """Stands in for GeminiClient during classification."""

    model = "fake-gemini"

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response if response is not None else classification()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response
