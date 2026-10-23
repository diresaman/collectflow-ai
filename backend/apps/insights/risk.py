"""
Client risk score (M13, v2 §6 "Risk score"): deterministic, explainable, 0-100. No AI and no model.

Factor maxima come from the v2 design. The mappings from facts to points were not in the design; they
were proposed in M13 and approved by the owner (docs/v2/09-m13-implementation-report.md, section 3):

| Factor                                   | Points                                                         |
| ---------------------------------------- | -------------------------------------------------------------- |
| Oldest unpaid invoice, days overdue      | round(35 x min(days, RISK_OVERDUE_FULL_DAYS) / FULL)           |
| Broken promises in the last 180 days     | 0 -> 0, 1 -> 15, 2 or more -> 25                               |
| Average days late on paid invoices       | round(20 x min(avg, RISK_LATE_FULL_DAYS) / FULL); fewer than   |
|                                          | RISK_MIN_PAID_HISTORY measured payments -> portfolio average,  |
|                                          | flagged low confidence; no measured payment at all -> 0         |
| No reply to the last two sends           | 5 per unanswered send (unanswered only after RISK_REPLY_WAIT_DAYS) |
| Open dispute                             | 10 if any dispute case is OPEN                                 |

Facts come only from the database: unpaid invoices still in the sheet (Google Sheets is authoritative);
the date a payment was seen = the first sync event that showed the invoice PAID; sent reminders; stored
replies; promise and dispute records. Rounding is half-up on Decimals, so results are reproducible.

`recompute()` is the only writer of the Client insight fields. It never drafts, approves, sends,
releases a hold or writes timeline events; it is safe to run any number of times.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.models import FollowUp
from apps.inbox.models import InboundMessage
from apps.invoices.models import Client, Invoice
from apps.timeline.models import EventType, TimelineEvent

FACTOR_MAX = {"oldest_overdue": 35, "broken_promises": 25, "days_late": 20, "no_reply": 10, "open_dispute": 10}
FACTOR_LABELS = {
    "oldest_overdue": "Oldest invoice overdue",
    "broken_promises": "Broken promises (180 days)",
    "days_late": "Average days late on paid invoices",
    "no_reply": "No reply to the last two reminders",
    "open_dispute": "Open dispute",
}
BROKEN_PROMISE_POINTS = {0: 0, 1: 15}  # 2 or more: the maximum
POINTS_PER_UNANSWERED_SEND = 5
LAST_SENDS = 2
# Serializes recomputes (any constant unique to this purpose).
ADVISORY_LOCK_KEY = 0x13_51_5C


def points(value: Decimal | int, full: int, maximum: int) -> int:
    """maximum x min(value, full) / full, rounded half-up; 0 for value <= 0."""
    value = Decimal(value)
    if value <= 0 or full <= 0:
        return 0
    return int((Decimal(maximum) * min(value, Decimal(full)) / Decimal(full)).quantize(Decimal(1), ROUND_HALF_UP))


def _rate(numerator: int, denominator: int) -> Decimal | None:
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"), ROUND_HALF_UP) if denominator else None


def _avg(values: list[int]) -> Decimal | None:
    return (Decimal(sum(values)) / Decimal(len(values))).quantize(Decimal("0.01"), ROUND_HALF_UP) if values else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass
class ClientFacts:
    """Everything the score of one client is computed from (gathered by `gather`)."""

    unpaid: list[Invoice] = field(default_factory=list)  # UNPAID and still in the sheet
    payments: list[dict[str, Any]] = field(default_factory=list)  # measured PAID invoices
    promises: list[PromiseToPay] = field(default_factory=list)
    sends: list[FollowUp] = field(default_factory=list)  # newest first
    replies: list[dt.datetime] = field(default_factory=list)
    open_disputes: list[DisputeCase] = field(default_factory=list)


@dataclass
class Result:
    score: int
    factors: list[dict[str, Any]]
    low_confidence: bool
    avg_days_late: Decimal | None
    promise_kept_rate: Decimal | None
    response_rate: Decimal | None
    outstanding: dict[str, str]
    oldest_days_overdue: int

    def as_json(self, today: dt.date) -> dict[str, Any]:
        return {
            "as_of": today.isoformat(),
            "factors": self.factors,
            "outstanding": self.outstanding,
            "oldest_days_overdue": self.oldest_days_overdue,
        }


def first_paid_seen() -> dict[Any, dt.datetime]:
    """invoice id -> when a sync first showed the invoice PAID (its INVOICE_UPDATED event)."""
    seen: dict[Any, dt.datetime] = {}
    events = TimelineEvent.objects.filter(
        type=EventType.INVOICE_UPDATED, payload__changes__status__to=Invoice.Status.PAID
    ).order_by("occurred_at", "id").values_list("invoice_id", "occurred_at")
    for invoice_id, occurred_at in events:
        seen.setdefault(invoice_id, occurred_at)
    return seen


def gather() -> dict[Any, ClientFacts]:
    """client id -> facts, for every client. A few bulk queries; no provider calls."""
    facts: dict[Any, ClientFacts] = defaultdict(ClientFacts)
    paid_seen = first_paid_seen()
    for invoice in Invoice.objects.order_by("due_date", "external_id"):
        if invoice.status == Invoice.Status.UNPAID and invoice.in_sheet:
            facts[invoice.client_id].unpaid.append(invoice)
        elif invoice.status == Invoice.Status.PAID and invoice.pk in paid_seen:
            paid_on = timezone.localdate(paid_seen[invoice.pk])
            facts[invoice.client_id].payments.append({
                "invoice_id": str(invoice.pk), "external_id": invoice.external_id,
                "due_date": invoice.due_date.isoformat(), "paid_seen_on": paid_on.isoformat(),
                "days_late": max((paid_on - invoice.due_date).days, 0),
            })
    for promise in PromiseToPay.objects.select_related("invoice").order_by("created_at", "id"):
        facts[promise.client_id].promises.append(promise)
    sent = FollowUp.objects.filter(status=FollowUp.Status.SENT, sent_at__isnull=False).select_related("invoice")
    for follow_up in sent.order_by("-sent_at", "-id"):
        facts[follow_up.invoice.client_id].sends.append(follow_up)
    for client_id, invoice_client_id, received_at in InboundMessage.objects.values_list(
        "client_id", "invoice__client_id", "received_at"
    ):
        facts[client_id].replies.append(received_at)
        if invoice_client_id != client_id:
            facts[invoice_client_id].replies.append(received_at)
    for case in DisputeCase.objects.filter(status=DisputeCase.Status.OPEN).select_related("invoice").order_by("created_at"):
        facts[case.client_id].open_disputes.append(case)
    return facts


def portfolio_lateness(all_facts: dict[Any, ClientFacts]) -> list[int]:
    return [p["days_late"] for f in all_facts.values() for p in f.payments]


def score(facts: ClientFacts, *, now: dt.datetime, portfolio: list[int]) -> Result:
    """The score of one client from its facts. Pure: same facts and time, same result."""
    today = timezone.localdate(now)
    factors: list[dict[str, Any]] = []

    def add(key: str, pts: int, detail: str, **data: Any) -> None:
        factors.append({"key": key, "label": FACTOR_LABELS[key], "points": pts, "max": FACTOR_MAX[key],
                        "detail": detail, "facts": data})

    # 1. Oldest unpaid invoice, days overdue.
    overdue = [i for i in facts.unpaid if i.due_date < today]
    oldest = min(overdue, key=lambda i: (i.due_date, i.external_id)) if overdue else None
    days = (today - oldest.due_date).days if oldest else 0
    add("oldest_overdue", points(days, settings.RISK_OVERDUE_FULL_DAYS, FACTOR_MAX["oldest_overdue"]),
        f"{oldest.external_id} is {days} days overdue (due {oldest.due_date.isoformat()})." if oldest
        else "No unpaid invoice is overdue.",
        invoice_id=str(oldest.pk) if oldest else None, external_id=oldest.external_id if oldest else None,
        due_date=_iso(oldest.due_date) if oldest else None, days_overdue=days,
        full_points_at_days=settings.RISK_OVERDUE_FULL_DAYS)

    # 2. Broken promises within the window.
    since = now - dt.timedelta(days=settings.RISK_BROKEN_PROMISE_WINDOW_DAYS)
    broken = [p for p in facts.promises if p.status == PromiseToPay.Status.BROKEN and p.resolved_at and p.resolved_at >= since]
    add("broken_promises", BROKEN_PROMISE_POINTS.get(len(broken), FACTOR_MAX["broken_promises"]),
        f"{len(broken)} promise(s) broken in the last {settings.RISK_BROKEN_PROMISE_WINDOW_DAYS} days." if broken
        else f"No promise broken in the last {settings.RISK_BROKEN_PROMISE_WINDOW_DAYS} days.",
        count=len(broken), window_days=settings.RISK_BROKEN_PROMISE_WINDOW_DAYS,
        promises=[{"promise_id": str(p.pk), "external_id": p.invoice.external_id,
                   "promised_date": _iso(p.promised_date), "broken_at": _iso(p.resolved_at)} for p in broken])

    # 3. Average days late on paid invoices (portfolio average below the minimum history).
    own = [p["days_late"] for p in facts.payments]
    own_avg, portfolio_avg = _avg(own), _avg(portfolio)
    low_confidence = len(own) < settings.RISK_MIN_PAID_HISTORY
    used = own_avg if not low_confidence else portfolio_avg
    source = "client" if not low_confidence else ("portfolio" if portfolio_avg is not None else "none")
    if source == "client":
        detail = f"Paid on average {own_avg} days late over {len(own)} payments."
    elif source == "portfolio":
        detail = (f"Only {len(own)} measured payment(s): uses the portfolio average of {portfolio_avg} days late "
                  f"({len(portfolio)} payments). Low confidence.")
    else:
        detail = "No measured payment yet (client or portfolio): no points. Low confidence."
    add("days_late", points(used or 0, settings.RISK_LATE_FULL_DAYS, FACTOR_MAX["days_late"]), detail,
        source=source, value_used=str(used) if used is not None else None, client_average=str(own_avg) if own_avg is not None else None,
        client_payments=facts.payments, portfolio_average=str(portfolio_avg) if portfolio_avg is not None else None,
        portfolio_payments=len(portfolio), min_history=settings.RISK_MIN_PAID_HISTORY, low_confidence=low_confidence,
        full_points_at_days=settings.RISK_LATE_FULL_DAYS)

    # 4. No reply to the last two sends.
    wait = dt.timedelta(days=settings.RISK_REPLY_WAIT_DAYS)
    checked = []
    for follow_up in facts.sends[:LAST_SENDS]:
        answered = any(received >= follow_up.sent_at for received in facts.replies)  # type: ignore[operator]
        pending = not answered and now - follow_up.sent_at < wait  # type: ignore[operator]
        checked.append({"follow_up_id": str(follow_up.pk), "external_id": follow_up.invoice.external_id,
                        "sent_at": _iso(follow_up.sent_at), "answered": answered, "waiting": pending})
    unanswered = sum(1 for s in checked if not s["answered"] and not s["waiting"])
    if not checked:
        detail = "No reminder sent yet."
    else:
        detail = f"{unanswered} of the last {len(checked)} reminder(s) unanswered after {settings.RISK_REPLY_WAIT_DAYS} days."
        if any(s["waiting"] for s in checked):
            detail += " A recent reminder is still inside the reply window."
    add("no_reply", unanswered * POINTS_PER_UNANSWERED_SEND, detail, sends=checked,
        reply_wait_days=settings.RISK_REPLY_WAIT_DAYS)

    # 5. Open dispute.
    add("open_dispute", FACTOR_MAX["open_dispute"] if facts.open_disputes else 0,
        f"Dispute open on {', '.join(c.invoice.external_id for c in facts.open_disputes)}." if facts.open_disputes
        else "No open dispute.",
        disputes=[{"dispute_id": str(c.pk), "external_id": c.invoice.external_id, "opened_at": _iso(c.created_at)}
                  for c in facts.open_disputes])

    # Metrics (the client's own history only).
    settled = [p for p in facts.promises if p.status in (PromiseToPay.Status.KEPT, PromiseToPay.Status.BROKEN)]
    kept = sum(1 for p in settled if p.status == PromiseToPay.Status.KEPT)
    measurable = [f for f in facts.sends if now - f.sent_at >= wait]  # type: ignore[operator]
    answered_all = sum(1 for f in measurable if any(r >= f.sent_at for r in facts.replies))  # type: ignore[operator]
    outstanding: dict[str, Decimal] = defaultdict(Decimal)
    for invoice in facts.unpaid:
        outstanding[invoice.currency] += invoice.amount
    return Result(
        score=sum(f["points"] for f in factors), factors=factors, low_confidence=low_confidence,
        avg_days_late=own_avg, promise_kept_rate=_rate(kept, len(settled)),
        response_rate=_rate(answered_all, len(measurable)),
        outstanding={currency: str(amount) for currency, amount in sorted(outstanding.items())},
        oldest_days_overdue=days,
    )


def compute_all(now: dt.datetime | None = None) -> dict[Any, Result]:
    now = now or timezone.now()
    all_facts = gather()
    portfolio = portfolio_lateness(all_facts)
    return {client_id: score(all_facts.get(client_id, ClientFacts()), now=now, portfolio=portfolio)
            for client_id in Client.objects.values_list("pk", flat=True)}


def recompute(now: dt.datetime | None = None) -> dict[str, int]:
    """Recompute every client's score and metrics. Idempotent; concurrent runs are serialized."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    changed = 0
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ADVISORY_LOCK_KEY])
        results = compute_all(now)
        for client in Client.objects.select_for_update().order_by("pk"):
            result = results[client.pk]
            values = {
                "risk_score": result.score, "risk_factors": result.as_json(today),
                "risk_low_confidence": result.low_confidence, "avg_days_late": result.avg_days_late,
                "promise_kept_rate": result.promise_kept_rate, "response_rate": result.response_rate,
            }
            if any(getattr(client, name) != value for name, value in values.items()):
                changed += 1
            Client.objects.filter(pk=client.pk).update(metrics_computed_at=now, **values)
    return {"clients": len(results), "changed": changed}
