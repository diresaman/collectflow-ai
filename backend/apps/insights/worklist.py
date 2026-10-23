"""
Prioritized client worklist (M13, v2 §6): priority = risk score x outstanding amount.

- One row per client and currency. Amounts are never added or compared across currencies: rows are
  ranked within their currency group only (priority, then score, then days overdue, then email).
- The score is the stored one (see `risk.recompute`, with its timestamp and facts); the outstanding
  amount is the live sum of the row's unpaid invoices still in the sheet (listed in the row).
- The suggested action is not a new rule: it is the most urgent M12 Today item for the row's invoices,
  in Today's fixed section order, with Today's "who can act" and "why you cannot" explanations. Drafts
  and approved reminders are suggested only for invoices that are currently eligible, so a high score
  never suggests chasing past a hold, a dispute, a payment or the approval rules.
- Read-only. Risk is decision support: nothing here drafts, approves, sends or releases anything.
"""

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.core import today as today_report
from apps.followups.eligibility import ineligibility
from apps.invoices.models import Client, Invoice
from apps.sequences.models import SequenceEnrollment

# Today sections that are actions on an invoice, in Today's order. "integrations" has no invoice and
# "held" is a blocker, not an action.
ACTION_SECTIONS = [key for key, _ in today_report.SECTIONS if key not in ("integrations", "held")]
# Today sections the worklist must never hide: shown as a banner above the ranking.
URGENT_SECTIONS = ("integrations", "replies", "deliveries")
CHASING_SECTIONS = ("drafts", "approved")
TOP_FACTORS = 3


def client_link(client_id: Any) -> str:
    return f"/clients?client={client_id}"


def top_factors(client: Client) -> list[dict[str, Any]]:
    factors = [f for f in (client.risk_factors or {}).get("factors", []) if f["points"] > 0]
    return sorted(factors, key=lambda f: (-f["points"], f["key"]))[:TOP_FACTORS]


def _fallback_action(invoices: list[Invoice], enrollments: dict[Any, SequenceEnrollment], today: dt.date) -> dict[str, Any]:
    """No Today item: say what the sequence will do next, or that nothing is due."""
    live = [enrollments[i.pk] for i in invoices if i.pk in enrollments]
    upcoming = sorted((e for e in live if e.next_action_at), key=lambda e: (e.next_action_at, str(e.pk)))
    if upcoming:
        e = upcoming[0]
        when = timezone.localtime(e.next_action_at).date()
        return {"code": "sequence_scheduled", "title": f"Nothing to do now: step {e.pending_step or e.next_step} of "
                f"{e.invoice.external_id} is due {when.isoformat()}", "detail": "The sequence drafts it; a person approves it.",
                "link": today_report._invoice_link(e.invoice_id), "who_can_act": "", "blocked": "",
                "invoice_external_id": e.invoice.external_id}
    if all(i.due_date >= today for i in invoices):
        return {"code": "not_overdue", "title": "Nothing to do now: no invoice is overdue yet", "detail": "",
                "link": "", "who_can_act": "", "blocked": "", "invoice_external_id": None}
    return {"code": "no_action", "title": "No action available now", "detail": "See the blockers below.",
            "link": today_report._invoice_link(invoices[0].pk), "who_can_act": "", "blocked": "",
            "invoice_external_id": invoices[0].external_id}


def _blockers(invoices: list[Invoice], enrollments: dict[Any, SequenceEnrollment],
              reasons: dict[Any, list[tuple[str, str]]]) -> list[dict[str, Any]]:
    blockers = []
    for invoice in invoices:
        for code, message in reasons[invoice.pk]:
            if code != "not_overdue":
                blockers.append({"invoice_id": str(invoice.pk), "invoice_external_id": invoice.external_id,
                                 "code": code, "detail": message})
        e = enrollments.get(invoice.pk)
        if e is not None and e.blocked_reason:
            detail = today_report.SEQUENCE_BLOCKS.get(e.blocked_reason, f"Sequence blocked: {e.blocked_reason}.")
            blockers.append({"invoice_id": str(invoice.pk), "invoice_external_id": invoice.external_id,
                             "code": e.blocked_reason, "detail": detail})
    return blockers


def build(user: Any, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    today = timezone.localdate(now)
    report = today_report.report(user, now)
    sections = {s["key"]: s["items"] for s in report["sections"]}

    unpaid = Invoice.objects.filter(status=Invoice.Status.UNPAID, in_sheet=True).order_by("due_date", "external_id")
    by_row: dict[tuple[Any, str], list[Invoice]] = defaultdict(list)
    for invoice in unpaid:
        by_row[(invoice.client_id, invoice.currency)].append(invoice)
    clients = Client.objects.in_bulk({client_id for client_id, _ in by_row})
    enrollments = {
        e.invoice_id: e for e in SequenceEnrollment.objects.filter(
            status__in=SequenceEnrollment.LIVE_STATUSES, invoice__in=unpaid).select_related("invoice")
    }
    reasons = {i.pk: ineligibility(i) for i in unpaid}
    eligible = {str(pk) for pk, found in reasons.items() if all(code == "not_overdue" for code, _ in found)}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unscored = []
    for (client_id, currency), invoices in by_row.items():
        client = clients[client_id]
        ids = {str(i.pk) for i in invoices}
        if client.risk_score is None:
            unscored.append({"client_id": str(client.pk), "client_name": client.display_name, "currency": currency})
        action = None
        for key in ACTION_SECTIONS:
            for item in sections[key]:
                if item.get("invoice_id") not in ids:
                    continue
                if key in CHASING_SECTIONS and item["invoice_id"] not in eligible:
                    continue
                action = {"code": key, "title": item["title"], "detail": item["detail"], "link": item["link"],
                          "who_can_act": item["who_can_act"], "blocked": item["blocked"],
                          "invoice_external_id": item["invoice_external_id"]}
                break
            if action:
                break
        score = client.risk_score or 0
        outstanding = sum((i.amount for i in invoices), Decimal(0))
        groups[currency].append({
            "client_id": str(client.pk), "client_name": client.display_name, "client_email": client.email,
            "invoice_names": sorted({i.client_name for i in invoices}), "currency": currency, "risk_score": client.risk_score, "low_confidence": client.risk_low_confidence,
            "outstanding": str(outstanding), "priority": str(score * outstanding),
            "oldest_days_overdue": max([(today - i.due_date).days for i in invoices] + [0]),
            "top_factors": top_factors(client), "computed_at": client.metrics_computed_at,
            "next_action": action or _fallback_action(invoices, enrollments, today),
            "blockers": _blockers(invoices, enrollments, reasons),
            "invoices": [{"id": str(i.pk), "external_id": i.external_id, "amount": str(i.amount), "due_date": i.due_date,
                          "days_overdue": max((today - i.due_date).days, 0), "link": today_report._invoice_link(i.pk),
                          "chasing_blocked": str(i.pk) not in eligible} for i in invoices],
            "link": client_link(client.pk),
        })
    for rows in groups.values():
        rows.sort(key=lambda r: (-Decimal(r["priority"]), -(r["risk_score"] or 0), -r["oldest_days_overdue"],
                                 r["client_email"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
    computed = [c.metrics_computed_at for c in clients.values() if c.metrics_computed_at]
    return {
        "generated_at": now,
        "scores_computed_at": min(computed) if computed else None,
        "urgent": [{"key": key, "title": dict(today_report.SECTIONS)[key], "count": len(sections[key])}
                   for key in URGENT_SECTIONS if sections[key]],
        "groups": [{"currency": currency, "rows": groups[currency]} for currency in sorted(groups)],
        "unscored": unscored,
    }
