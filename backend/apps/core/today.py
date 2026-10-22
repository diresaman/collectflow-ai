"""
Today (M12): the work an operator can act on now, from existing records only.

No scores and no AI: fixed sections in a fixed order, each sorted deterministically (oldest or most
overdue first). Every item says what it is, why it needs attention, who may act, and links to the
screen where the action happens. Read-only; nothing is computed by calling a provider.
"""

import datetime as dt
from typing import Any

from django.utils import timezone

from apps.accounts import approval
from apps.accounts.models import Role
from apps.accounts.roles import has_role
from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.models import FollowUp
from apps.inbox.models import InboundMessage, Task
from apps.invoices.models import ChaseHold, Invoice
from apps.sequences.models import SequenceEnrollment

from . import integrations

BROKEN_PROMISE_DAYS = 14
STUCK_SENDING_MINUTES = 15

TRIAGE_REASONS = {
    "review_required": "Promise or dispute: always reviewed by a person",
    "low_confidence": "AI not confident enough",
    "suspected_injection": "Text tries to instruct the AI",
    "unknown_sender": "Unknown sender",
    "several_invoices": "Concerns more than one invoice",
    "no_automatic_action": "No automatic action fits",
    "possible_payment_or_dispute": "Mentions a payment or dispute",
    "invalid_output": "AI answer failed validation",
    "ai_retry_scheduled": "AI unavailable; retry scheduled",
}
SEQUENCE_BLOCKS = {
    "delivery_unknown": "Delivery outcome unknown: reconcile it (never resent automatically).",
    "delivery_failed": "The last reminder was not delivered: draft the step again.",
    "generation_failed": "The AI draft failed: draft the step manually.",
}


def _invoice_link(invoice_id: Any) -> str:
    return f"/?invoice={invoice_id}"


def _item(*, key: str, title: str, detail: str, since: dt.datetime | None, link: str, who: str,
          invoice: Invoice | None = None, blocked: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "key": key, "title": title, "detail": detail, "since": since, "link": link, "who_can_act": who,
        "invoice_id": str(invoice.pk) if invoice else None,
        "invoice_external_id": invoice.external_id if invoice else None,
        "client_name": invoice.client_name if invoice else None,
        "blocked": blocked, **extra,
    }


def integration_issues(now: dt.datetime) -> list[dict[str, Any]]:
    report = integrations.report(now)
    items = [
        _item(key=f"integration:{i['key']}", title=f"{i['name']}: {i['status'].replace('_', ' ')}", detail=i["detail"],
              since=i["last_attempt_at"], link="/settings/integrations", who="Admin", action=i["action"])
        for i in report["integrations"] if i["status"] in ("failed", "stale")
    ]
    items += [
        _item(key=f"job:{j['kind']}", title=f"Scheduled job {j['name']}: {j['status']}",
              detail=f"Last finished {j['last_completed_at'] or 'never'}" + (f" ({j['last_error_code']})" if j["last_error_code"] else ""),
              since=j["last_completed_at"], link="/settings/integrations", who="Admin")
        for j in report["scheduled_jobs"] if j["status"] in ("failed", "stale")
    ]
    return items


def replies_to_review() -> list[dict[str, Any]]:
    replies = InboundMessage.objects.filter(state__in=[InboundMessage.State.TRIAGE, InboundMessage.State.RECEIVED]) \
        .select_related("invoice", "client").order_by("received_at", "id")
    return [
        _item(key=f"reply:{r.pk}", invoice=r.invoice, since=r.received_at, link=f"/inbox?reply={r.pk}",
              title=f"Reply from {r.client.display_name}",
              detail=(TRIAGE_REASONS.get(r.triage_reason, r.triage_reason.replace("_", " ")) if r.state == "TRIAGE"
                      else "Being classified") + (f" · AI: {r.get_label_display()}" if r.label else ""),
              who="Approver" if r.label in ("PROMISE_TO_PAY", "DISPUTE", "CLAIMS_PAID") else "Operator")
        for r in replies
    ]


def drafts(user: Any) -> list[dict[str, Any]]:
    items = []
    for f in FollowUp.objects.filter(status=FollowUp.Status.DRAFT).select_related("invoice", "step").order_by("generated_at", "id"):
        need = approval.requirement(f)
        refused = approval.refusal(f, user)
        items.append(_item(
            key=f"draft:{f.pk}", invoice=f.invoice, since=f.generated_at, link=_invoice_link(f.invoice_id),
            title=f"Draft to review: {f.subject}",
            detail=f"{f.get_tone_display()} tone" + (f", step {f.step.order}" if f.step else "")
                   + (f". Needs an approver: {'; '.join(need.reasons)}" if need.four_eyes else ""),
            who="Approver" if need.four_eyes else "Operator",
            blocked=refused[1] if refused else "", follow_up_id=str(f.pk),
        ))
    return items


def approved_to_send() -> list[dict[str, Any]]:
    return [
        _item(key=f"approved:{f.pk}", invoice=f.invoice, since=f.approved_at or f.updated_at, link=_invoice_link(f.invoice_id),
              title=f"Approved, not sent: {f.subject}", detail="Send it after a final check (sending is always a separate click).",
              who="Operator", follow_up_id=str(f.pk))
        for f in FollowUp.objects.filter(status=FollowUp.Status.APPROVED).select_related("invoice").order_by("approved_at", "id")
    ]


def deliveries_to_reconcile(now: dt.datetime) -> list[dict[str, Any]]:
    stuck = now - dt.timedelta(minutes=STUCK_SENDING_MINUTES)
    rows = FollowUp.objects.filter(status=FollowUp.Status.DELIVERY_UNKNOWN) | FollowUp.objects.filter(
        status=FollowUp.Status.SENDING, updated_at__lt=stuck)
    return [
        _item(key=f"delivery:{f.pk}", invoice=f.invoice, since=f.updated_at, link=_invoice_link(f.invoice_id),
              title=f"Delivery outcome unknown: {f.subject}",
              detail="Check the sent folder in Gmail, then reconcile with `manage.py reconcile_delivery`. It is never resent automatically.",
              who="Admin", follow_up_id=str(f.pk))
        for f in rows.select_related("invoice").order_by("updated_at", "id")
    ]


def broken_promises(now: dt.datetime) -> list[dict[str, Any]]:
    since = now - dt.timedelta(days=BROKEN_PROMISE_DAYS)
    promises = PromiseToPay.objects.filter(status=PromiseToPay.Status.BROKEN, resolved_at__gte=since,
                                           invoice__status=Invoice.Status.UNPAID).select_related("invoice")
    return [
        _item(key=f"broken:{p.pk}", invoice=p.invoice, since=p.resolved_at, link=_invoice_link(p.invoice_id),
              title=f"Promise broken: {p.amount} {p.currency} by {p.promised_date.isoformat()}",
              detail="The next reminder is one tone firmer and mentions the missed date; review it when drafted, or call.",
              who="Operator")
        for p in promises.order_by("resolved_at", "id")
    ]


def promises_due(today: dt.date) -> list[dict[str, Any]]:
    promises = PromiseToPay.objects.filter(status=PromiseToPay.Status.OPEN, promised_date__lte=today).select_related("invoice")
    return [
        _item(key=f"promise:{p.pk}", invoice=p.invoice, since=p.created_at, link=_invoice_link(p.invoice_id),
              title=f"Payment promised by {p.promised_date.isoformat()}: {p.amount} {p.currency}",
              detail="Settled automatically from the sheet within the 2-day grace period; check the payment if it is late.",
              who="Operator")
        for p in promises.order_by("promised_date", "id")
    ]


def open_disputes() -> list[dict[str, Any]]:
    cases = DisputeCase.objects.filter(status=DisputeCase.Status.OPEN).select_related("invoice", "owner")
    rows = sorted(cases, key=lambda c: (c.owner_id is not None, c.created_at, str(c.pk)))  # unassigned first
    return [
        _item(key=f"dispute:{c.pk}", invoice=c.invoice, since=c.created_at, link=f"/disputes?case={c.pk}",
              title=f"Dispute: {c.get_reason_display()}",
              detail=(f"Owner {c.owner.get_full_name() or c.owner.username}" if c.owner else "No owner yet")
                     + ". Chasing is blocked until an approver resolves it.",
              who="Approver (resolve), operator (notes, owner)", dispute_id=str(c.pk))
        for c in rows
    ]


def tasks_due(now: dt.datetime) -> list[dict[str, Any]]:
    end_of_day = timezone.make_aware(dt.datetime.combine(timezone.localdate(now), dt.time.max))
    tasks = Task.objects.filter(status=Task.Status.OPEN, due_at__lte=end_of_day).select_related("invoice", "assignee")
    return [
        _item(key=f"task:{t.pk}", invoice=t.invoice, since=t.due_at, link=f"/tasks?task={t.pk}",
              title=t.title, detail=f"{t.get_kind_display()} · {'overdue' if t.due_at < now else 'due today'}"
              + (f" · {t.assignee.get_full_name() or t.assignee.username}" if t.assignee else " · unassigned"),
              who="Operator", task_id=str(t.pk), overdue=t.due_at < now)
        for t in tasks.order_by("due_at", "id")
    ]


def held_invoices() -> list[dict[str, Any]]:
    holds = ChaseHold.objects.filter(released_at__isnull=True).select_related("invoice").order_by("created_at", "id")
    by_invoice: dict[Any, list[ChaseHold]] = {}
    for hold in holds:
        by_invoice.setdefault(hold.invoice_id, []).append(hold)
    return [
        _item(key=f"held:{invoice_id}", invoice=items[0].invoice, since=items[0].created_at, link=_invoice_link(invoice_id),
              title=f"Chasing on hold: {', '.join(h.get_reason_display().lower() for h in items)}",
              detail=" ".join(h.release_rule for h in items),
              who="Approver" if any(h.protected or h.dispute_id or h.promise_id for h in items) else "Operator",
              holds=[{"reason": h.reason, "source": h.source, "release_rule": h.release_rule} for h in items])
        for invoice_id, items in by_invoice.items()
    ]


def sequence_blocks() -> list[dict[str, Any]]:
    enrollments = SequenceEnrollment.objects.filter(status__in=SequenceEnrollment.LIVE_STATUSES).select_related("invoice")
    items = []
    for e in enrollments.order_by("updated_at", "id"):
        if e.blocked_reason in SEQUENCE_BLOCKS:
            items.append(_item(key=f"block:{e.pk}", invoice=e.invoice, since=e.updated_at, link=_invoice_link(e.invoice_id),
                               title=f"Sequence step {e.pending_step or e.next_step} held", detail=SEQUENCE_BLOCKS[e.blocked_reason],
                               who="Operator"))
        elif e.status == SequenceEnrollment.Status.PAUSED and e.pause_reason == "history_review":
            items.append(_item(key=f"review:{e.pk}", invoice=e.invoice, since=e.updated_at, link=_invoice_link(e.invoice_id),
                               title="Sequence position needs review", detail=e.review_reason, who="Approver"))
    return items


SECTIONS = [
    ("integrations", "Integration problems"),
    ("replies", "Customer replies to review"),
    ("disputes", "Open disputes"),
    ("deliveries", "Deliveries with an unknown outcome"),
    ("drafts", "Drafts awaiting approval"),
    ("approved", "Approved reminders not sent yet"),
    ("broken_promises", "Broken promises"),
    ("promises_due", "Promised payments due"),
    ("tasks", "Tasks due today or overdue"),
    ("sequence_blocks", "Sequences that need a person"),
    ("held", "Invoices on hold"),
]


def report(user: Any, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    builders = {
        "integrations": lambda: integration_issues(now),
        "replies": replies_to_review,
        "disputes": open_disputes,
        "deliveries": lambda: deliveries_to_reconcile(now),
        "drafts": lambda: drafts(user),
        "approved": approved_to_send,
        "broken_promises": lambda: broken_promises(now),
        "promises_due": lambda: promises_due(timezone.localdate(now)),
        "tasks": lambda: tasks_due(now),
        "sequence_blocks": sequence_blocks,
        "held": held_invoices,
    }
    sections = [{"key": key, "title": title, "items": builders[key]()} for key, title in SECTIONS]
    return {
        "generated_at": now,
        "role": "APPROVER" if has_role(user, Role.APPROVER) else "OPERATOR",
        "total": sum(len(s["items"]) for s in sections),
        "sections": sections,
    }
