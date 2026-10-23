"""
Consolidated client statements (M14, v2 §3 "Statement email", §5 StatementLine, §6 four-eyes).

A statement is a `FollowUp` with kind STATEMENT and one `StatementLine` per invoice. It reuses the
reminder review, approval and Gmail delivery path; this module only adds what differs:

- Eligibility: a client with at least STATEMENT_MIN_INVOICES invoices that could each be chased now
  (unpaid, in the sheet, overdue, no hold or dispute, not in another statement), have no approved or
  unresolved reminder, and are outside the reminder cooldown. Unsent per-invoice drafts of those
  invoices are withdrawn when the statement is saved (the sequence step stays pending).
- Content: Gemini writes only the subject, opening and closing; the backend writes the invoice lines
  and per-currency totals, so no figure comes from the AI (AI text containing amounts is rejected).
  The lines block must still be in the body, unchanged, when it is edited, approved and delivered.
- Approval: always an APPROVER who is not the last editor (apps.accounts.approval).
- Approval, send reservation and delivery re-check every line invoice under its lock: eligibility and
  the facts each line was written from. Any change blocks the statement for a person; nothing stale
  is sent. While a statement is active its invoices get no separate reminder (`in_statement`).
- Preparing a statement is always a person's request; no scheduled job prepares, approves or sends.
"""

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.automation import outbox
from apps.invoices.models import Client, Invoice
from apps.timeline import services as timeline
from apps.timeline.models import EventType
from integrations.gemini import (
    GeminiClient,
    GeminiError,
    GeminiNotConfigured,
    GeminiQuotaExceeded,
    GeminiResponseError,
)

from . import prompts
from .eligibility import changed_snapshot_fields, ineligibility, invoice_snapshot
from .models import FollowUp, GenerationAttempt, StatementLine, Tone

STATEMENT_JOB_KIND = "followups.statement"
STATEMENT_TONE = Tone.PROFESSIONAL
RECOMMENDED_ACTION = "Check the invoice lines and totals; an approver who did not edit it approves it, then send."
LINES_START = "Outstanding invoices:"
# Figures the AI must not write: decimal amounts and currency symbols next to digits (codes are added
# per statement, see `_figures_pattern`).
DECIMAL_AMOUNT = r"\d[\d,]*[.,]\d{2}\b"
CURRENCY_SYMBOL = r"[$€£¥₦₹]\s?\d"


class StatementError(Exception):
    pass


@dataclass
class Candidate:
    invoice: Invoice
    reasons: list[str] = field(default_factory=list)
    replaces_draft_id: Any = None

    @property
    def eligible(self) -> bool:
        return not self.reasons


def last_sent_covering(invoice: Invoice) -> dt.datetime | None:
    """The latest SENT reminder or statement that covered this invoice."""
    return FollowUp.objects.filter(
        Q(invoice=invoice, kind=FollowUp.Kind.INVOICE) | Q(kind=FollowUp.Kind.STATEMENT, statement_lines__invoice=invoice),
        status=FollowUp.Status.SENT,
    ).aggregate(m=Max("sent_at"))["m"]


def candidates(client: Client, now: dt.datetime | None = None) -> list[Candidate]:
    """Every unpaid invoice of the client still in the sheet, with why it cannot be in a statement."""
    now = now or timezone.now()
    cooldown = dt.timedelta(days=settings.FOLLOWUP_COOLDOWN_DAYS)
    result = []
    for invoice in Invoice.objects.filter(client=client, status=Invoice.Status.UNPAID, in_sheet=True).order_by(
        "due_date", "external_id"
    ):
        candidate = Candidate(invoice, [message for _, message in ineligibility(invoice)])
        active = FollowUp.objects.filter(invoice=invoice, kind=FollowUp.Kind.INVOICE,
                                         status__in=FollowUp.ACTIVE_STATUSES).first()
        if active is not None and active.status == FollowUp.Status.DRAFT:
            candidate.replaces_draft_id = active.pk
        elif active is not None:
            candidate.reasons.append(f"It has a reminder that is {active.get_status_display().lower()}.")
        sent = last_sent_covering(invoice)
        if sent is not None and now - sent < cooldown:
            until = timezone.localtime(sent + cooldown).date().isoformat()
            candidate.reasons.append(f"A reminder was sent recently; cooldown until {until}.")
        result.append(candidate)
    return result


def totals(lines: list[Any]) -> dict[str, Decimal]:
    """Per-currency totals. Different currencies are never added together."""
    sums: dict[str, Decimal] = {}
    for line in lines:
        sums[line.currency] = sums.get(line.currency, Decimal(0)) + line.amount
    return dict(sorted(sums.items()))


def render_lines(lines: list[Any]) -> str:
    """The exact invoice list and totals inserted by the backend (and required in the sent body)."""
    rows = [LINES_START]
    rows += [f"- {line.external_id}: {line.amount:,.2f} {line.currency}, due {line.due_date.isoformat()}"
             for line in lines]
    rows += [f"Total outstanding ({currency}): {amount:,.2f} {currency}" for currency, amount in totals(lines).items()]
    return "\n".join(rows)


def preview(client: Client) -> dict[str, Any]:
    """What a statement would contain now. No AI call, no writes."""
    found = candidates(client)
    eligible = [c for c in found if c.eligible]
    active = active_statement_for(client)
    reason = ""
    if active is not None:
        reason = "This client already has a statement that is not sent yet."
    elif len(eligible) < settings.STATEMENT_MIN_INVOICES:
        reason = f"A statement needs at least {settings.STATEMENT_MIN_INVOICES} invoices that can be chased now."
    return {
        "client_id": str(client.pk),
        "can_prepare": not reason,
        "reason": reason,
        "min_invoices": settings.STATEMENT_MIN_INVOICES,
        "lines": [_line_dict(c.invoice) | {"replaces_draft_id": str(c.replaces_draft_id) if c.replaces_draft_id else None}
                  for c in eligible],
        "totals": {k: str(v) for k, v in totals([c.invoice for c in eligible]).items()},
        "excluded": [_line_dict(c.invoice) | {"reasons": c.reasons} for c in found if not c.eligible],
        "active_statement_id": str(active.pk) if active else None,
    }


def _line_dict(invoice: Invoice) -> dict[str, Any]:
    return {"invoice_id": str(invoice.pk), "external_id": invoice.external_id, "amount": str(invoice.amount),
            "currency": invoice.currency, "due_date": invoice.due_date.isoformat(), "link": f"/?invoice={invoice.pk}"}


def active_statement_for(client: Client) -> FollowUp | None:
    return FollowUp.objects.filter(kind=FollowUp.Kind.STATEMENT, status__in=FollowUp.ACTIVE_STATUSES,
                                   invoice__client=client).first()


# Request and generation -----------------------------------------------------------------------


def request(client_id: Any, *, actor: Any) -> Any:
    """A person asks for a statement: checked now (no AI), drafted by the worker."""
    from .services import ActiveFollowUpExists, GenerationInProgress, NotEligible

    client = Client.objects.get(pk=client_id)  # DoesNotExist -> 404
    info = preview(client)
    if info["active_statement_id"]:
        raise ActiveFollowUpExists(info["reason"], follow_up_id=info["active_statement_id"])
    if not info["can_prepare"]:
        raise NotEligible(info["reason"], excluded=info["excluded"])
    with transaction.atomic():
        queued = outbox.active_job(STATEMENT_JOB_KIND, client_id=str(client.pk))
        if queued is not None:
            raise GenerationInProgress("A statement is already being prepared for this client.", job_id=str(queued.pk))
        return outbox.enqueue(
            STATEMENT_JOB_KIND,
            {"client_id": str(client.pk), "invoice_ids": [line["invoice_id"] for line in info["lines"]]},
            max_attempts=1, created_by=actor,
        )


def handle_statement_job(job: Any) -> dict[str, Any]:
    from .services import FollowUpError

    try:
        follow_up = generate(uuid.UUID(job.payload["client_id"]), job.payload["invoice_ids"], actor=job.created_by)
    except Client.DoesNotExist:
        raise outbox.JobFailed("not_found", {"code": "not_found", "detail": "Client not found.", "http_status": 404}) from None
    except FollowUpError as error:
        raise outbox.JobFailed(
            error.code, {"code": error.code, "detail": error.message, "http_status": error.status_code, **error.extra}
        ) from None
    return {"follow_up_id": str(follow_up.pk)}


def _figures_pattern(currencies: list[str]) -> re.Pattern[str]:
    codes = "|".join(re.escape(c) for c in currencies) or "XXX"
    return re.compile(rf"{DECIMAL_AMOUNT}|{CURRENCY_SYMBOL}|\b(?:{codes})\s?\d|\d\s?(?:{codes})\b")


def validate_statement_output(data: dict[str, Any], currencies: list[str]) -> dict[str, str]:
    from .services import URL_PATTERN, AIBadResponse, InvalidContent, validate_subject

    try:
        subject = validate_subject(data.get("subject"))
    except InvalidContent as exc:
        raise AIBadResponse(f"Gemini returned an invalid statement: {exc.message}") from None
    texts = {"subject": subject}
    for name in ("opening", "closing", "reasoning"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 1500:
            raise AIBadResponse(f"Gemini returned an invalid {name}.")
        texts[name] = value.strip()
    figures = _figures_pattern(currencies)
    for name in ("subject", "opening", "closing"):
        if URL_PATTERN.search(texts[name]):
            raise AIBadResponse("Gemini included a link, which is not allowed in statements.")
        if figures.search(texts[name]):
            raise AIBadResponse("Gemini wrote an amount; statement figures come only from the backend.")
    return texts


def generate(client_id: uuid.UUID, invoice_ids: list[str], *, actor: Any = None, gemini: Any = None) -> FollowUp:
    from .services import AIBadResponse, AINotConfigured, AIQuotaExhausted, AIUnavailable, FollowUpError, StaleInvoice

    client = Client.objects.get(pk=client_id)
    invoices = list(Invoice.objects.filter(pk__in=invoice_ids, client=client).order_by("due_date", "external_id"))
    primary = invoices[0] if invoices else Invoice.objects.filter(client=client).first()
    _require_same_set(client, invoice_ids)  # refused before any AI call: not a generation attempt
    if primary is None:
        raise StaleInvoice("The statement has no invoices.")
    snapshots = {str(i.pk): invoice_snapshot(i) for i in invoices}
    currencies = sorted({i.currency for i in invoices})

    model_name = ""
    try:
        client_ai = gemini or GeminiClient.from_settings()
        model_name = client_ai.model
        data = client_ai.generate_json(
            system_instruction=prompts.STATEMENT_SYSTEM_INSTRUCTION,
            prompt=prompts.build_statement_prompt(prompts.build_statement_context(client.display_name, invoices)),
            response_schema=prompts.statement_response_schema(),
        )
        texts = validate_statement_output(data, currencies)
    except GeminiError as exc:
        error: FollowUpError
        if isinstance(exc, GeminiResponseError):
            error = AIBadResponse(str(exc))
        elif isinstance(exc, GeminiQuotaExceeded):
            error = AIQuotaExhausted(str(exc))
        elif isinstance(exc, GeminiNotConfigured):
            error = AINotConfigured(str(exc))
        else:
            error = AIUnavailable(str(exc))
        _record_failure(primary, error, model_name, actor)
        raise error from None
    except AIBadResponse as exc:
        _record_failure(primary, exc, model_name, actor)
        raise

    try:
        follow_up = _persist(client, invoice_ids, snapshots, texts, model_name, actor)
    except FollowUpError as exc:
        _record_failure(primary, exc, model_name, actor)
        raise
    except IntegrityError:
        error = StaleInvoice("Another reminder or statement was created for these invoices at the same time.")
        _record_failure(primary, error, model_name, actor)
        raise error from None
    GenerationAttempt.objects.create(invoice=follow_up.invoice, follow_up=follow_up, outcome=GenerationAttempt.Outcome.SUCCEEDED,
                                     purpose=GenerationAttempt.Purpose.STATEMENT, ai_model=model_name,
                                     prompt_version=prompts.STATEMENT_PROMPT_VERSION)
    return follow_up


def _require_same_set(client: Client, invoice_ids: list[str]) -> None:
    """The invoices requested must still be exactly the eligible ones (nothing added, nothing dropped)."""
    from .services import StaleInvoice

    eligible = {str(c.invoice.pk) for c in candidates(client) if c.eligible}
    if eligible != set(invoice_ids) or len(eligible) < settings.STATEMENT_MIN_INVOICES:
        raise StaleInvoice("The client's eligible invoices changed since the statement was requested. Prepare it again.")


def _persist(client: Client, invoice_ids: list[str], snapshots: dict[str, dict[str, str]], texts: dict[str, str],
             model_name: str, actor: Any) -> FollowUp:
    from .services import StaleInvoice, _conditional_transition, validate_body

    with transaction.atomic():
        # Lock order as everywhere: unsent reminders, then invoices (by primary key).
        unsent = FollowUp.objects.filter(
            Q(invoice_id__in=invoice_ids) | Q(statement_lines__invoice_id__in=invoice_ids),
            status__in=[FollowUp.Status.DRAFT, FollowUp.Status.APPROVED]).values("pk")
        list(FollowUp.objects.select_for_update(of=("self",)).filter(pk__in=unsent).order_by("pk"))
        locked = list(Invoice.objects.select_for_update().filter(pk__in=invoice_ids).order_by("pk"))
        for invoice in locked:
            changed = changed_snapshot_fields(snapshots[str(invoice.pk)], invoice)
            if changed:
                raise StaleInvoice(f"{invoice.external_id} changed while the statement was drafted ({', '.join(changed)}).")
        _require_same_set(client, invoice_ids)

        lines = sorted(locked, key=lambda i: (i.due_date, i.external_id))
        body = validate_body("\n\n".join([texts["opening"], render_lines(lines), texts["closing"]]))
        # The statement replaces the invoices' unsent per-invoice drafts (their steps stay pending).
        for draft in FollowUp.objects.filter(invoice_id__in=invoice_ids, kind=FollowUp.Kind.INVOICE,
                                             status=FollowUp.Status.DRAFT):
            _conditional_transition(draft, FollowUp.Status.REJECTED, cancelled_reason="statement")
            timeline.record(EventType.FOLLOW_UP_CANCELLED, actor=actor, invoice=draft.invoice,
                            follow_up_id=str(draft.pk), previous_status="DRAFT", reason="statement")
        follow_up = FollowUp.objects.create(
            invoice=lines[0], kind=FollowUp.Kind.STATEMENT, subject=texts["subject"], body=body, tone=STATEMENT_TONE,
            reasoning=texts["reasoning"], recommended_action=RECOMMENDED_ACTION,
            invoice_snapshot=snapshots[str(lines[0].pk)], ai_model=model_name,
            prompt_version=prompts.STATEMENT_PROMPT_VERSION,
        )
        StatementLine.objects.bulk_create([
            StatementLine(follow_up=follow_up, invoice=invoice, amount=invoice.amount, currency=invoice.currency,
                          due_date=invoice.due_date, invoice_snapshot=snapshots[str(invoice.pk)], position=n)
            for n, invoice in enumerate(lines, 1)
        ])
        for invoice in lines:
            timeline.record(EventType.DRAFT_GENERATED, actor=actor, invoice=invoice, follow_up_id=str(follow_up.pk),
                            kind=FollowUp.Kind.STATEMENT, tone=STATEMENT_TONE, ai_model=model_name,
                            prompt_version=prompts.STATEMENT_PROMPT_VERSION, statement_lines=len(lines))
        return follow_up


def _record_failure(invoice: Invoice | None, error: Any, model_name: str, actor: Any) -> None:
    if invoice is None:
        return
    with transaction.atomic():
        GenerationAttempt.objects.create(invoice=invoice, outcome=GenerationAttempt.Outcome.FAILED,
                                         purpose=GenerationAttempt.Purpose.STATEMENT, error_code=error.code,
                                         error_message=error.message[:500], ai_model=model_name,
                                         prompt_version=prompts.STATEMENT_PROMPT_VERSION)
        timeline.record(EventType.DRAFT_GENERATION_FAILED, actor=actor, invoice=invoice, error_code=error.code,
                        kind=FollowUp.Kind.STATEMENT)


# Checks shared by edit, approval, reservation and delivery ------------------------------------------


def lines_of(follow_up: FollowUp) -> list[StatementLine]:
    return list(StatementLine.objects.filter(follow_up=follow_up).select_related("invoice").order_by("position"))


def require_lines_block(follow_up: FollowUp, body: str | None = None) -> None:
    from .services import InvalidContent

    block = render_lines([_LineView(line) for line in lines_of(follow_up)])
    if block not in (follow_up.body if body is None else body):
        raise InvalidContent("The invoice list and totals of a statement must stay exactly as prepared. "
                             "Edit only the text around them.")


@dataclass
class _LineView:
    """A statement line rendered with the facts it was written from (not the invoice's current ones)."""

    line: StatementLine

    @property
    def external_id(self) -> str:
        return self.line.invoice_snapshot.get("external_id", self.line.invoice.external_id)

    @property
    def amount(self) -> Decimal:
        return self.line.amount

    @property
    def currency(self) -> str:
        return self.line.currency

    @property
    def due_date(self) -> dt.date:
        return self.line.due_date


def check_lines(follow_up: FollowUp, error: Any, prefix: str, *, lock: bool) -> list[Invoice]:
    """
    Every line invoice must still be chaseable (holds, disputes, payment, sheet, other statements) and
    unchanged since the statement was written, all for one recipient; the lines block must be intact.
    With `lock`, the invoices are locked (by primary key) for the caller's transaction.
    """
    from .services import InvalidContent, StaleInvoice, refuse_if_ineligible

    lines = lines_of(follow_up)
    query = Invoice.objects.filter(pk__in=[line.invoice_id for line in lines]).order_by("pk")
    invoices = {i.pk: i for i in (query.select_for_update() if lock else query)}
    if len(lines) < settings.STATEMENT_MIN_INVOICES or len(invoices) != len(lines):
        raise InvalidContent("This statement has no valid invoice lines.")
    for line in lines:
        invoice = invoices[line.invoice_id]
        refuse_if_ineligible(invoice, error, f"{prefix}{invoice.external_id}: ", statement=follow_up.pk)
        changed = changed_snapshot_fields(line.invoice_snapshot, invoice)
        if changed:
            raise StaleInvoice(f"{invoice.external_id} changed since this statement was prepared ({', '.join(changed)}). "
                               "Reject it and prepare a new statement.", changed_fields=changed)
    recipients = {str(line.invoice_snapshot.get("client_email", "")).lower() for line in lines}
    if recipients != {str(follow_up.invoice_snapshot.get("client_email", "")).lower()}:
        raise StaleInvoice("The statement's invoices no longer share one recipient.")
    require_lines_block(follow_up)
    return [invoices[line.invoice_id] for line in lines]


def statement_follow_ups_for(invoice_id: Any) -> Any:
    """Unsent statements that include the invoice (withdrawn with its other reminders by M11 cases)."""
    return FollowUp.objects.filter(kind=FollowUp.Kind.STATEMENT, statement_lines__invoice_id=invoice_id,
                                   status__in=[FollowUp.Status.DRAFT, FollowUp.Status.APPROVED])
