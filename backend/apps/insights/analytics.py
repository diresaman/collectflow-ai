"""
Accounts-receivable analytics and the 30-day cash forecast (M14, v2 §3 "Analytics", §7H).

Deterministic and explainable: no AI, no model. Every figure is per currency (never added across
currencies) and is computed from the database only:

- an invoice is "billed" when CollectFlow first saw it (its INVOICE_CREATED event): the sheet has no
  invoice-date column;
- an invoice is "paid" when a sync first showed it PAID (the INVOICE_UPDATED event): this is the date
  the sheet was observed, not the bank settlement date;
- invoices no longer in the sheet are left out of every figure (their fate is unknown).

From the design: the aging buckets (current, 1-30, 31-60, 61-90, 90+) and the forecast rule
(outstanding x P(paid within 30 days); an open promise inside the window uses the client's
promise-kept rate; otherwise P comes from the aging bucket and the client's average days late;
fewer than 3 paid invoices -> portfolio averages, low confidence). The DSO, CEI and attribution
formulas and the forecast probabilities are not in the design; the conservative choices below are
documented in docs/v2/10-m14-implementation-report.md, section 3, and tunable in settings.
"""

import datetime as dt
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.cases.models import DisputeCase, PromiseToPay
from apps.followups.models import FollowUp
from apps.invoices.models import ChaseHold, Invoice
from apps.timeline.models import EventType, TimelineEvent

from .models import AnalyticsSnapshot
from .risk import ADVISORY_LOCK_KEY, first_paid_seen

BUCKETS = [("current", "Current (not yet due)"), ("1_30", "1-30 days"), ("31_60", "31-60 days"),
           ("61_90", "61-90 days"), ("90_plus", "Over 90 days")]
CENT = Decimal("0.01")


def bucket_of(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1_30"
    if days_overdue <= 60:
        return "31_60"
    if days_overdue <= 90:
        return "61_90"
    return "90_plus"


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, ROUND_HALF_UP))


def first_seen() -> dict[Any, dt.datetime]:
    """
    invoice id -> when CollectFlow first saw it: its INVOICE_CREATED event, or the row's creation time
    when no event exists (the row is created by that same import).
    """
    seen: dict[Any, dt.datetime] = dict(Invoice.objects.values_list("pk", "created_at"))
    for invoice_id, at in TimelineEvent.objects.filter(type=EventType.INVOICE_CREATED).values_list("invoice_id", "occurred_at"):
        if invoice_id in seen:
            seen[invoice_id] = min(seen[invoice_id], at)
    return seen


def _outstanding() -> list[Invoice]:
    return list(Invoice.objects.filter(status=Invoice.Status.UNPAID, in_sheet=True).order_by("due_date", "external_id"))


# Aging ------------------------------------------------------------------------------------------


def aging(now: dt.datetime) -> dict[str, Any]:
    today = timezone.localdate(now)
    by_currency: dict[str, dict[str, dict[str, Any]]] = {}
    for invoice in _outstanding():
        days = (today - invoice.due_date).days
        row = by_currency.setdefault(invoice.currency, {key: {"amount": Decimal(0), "count": 0, "invoice_ids": []}
                                                        for key, _ in BUCKETS})[bucket_of(days)]
        row["amount"] += invoice.amount
        row["count"] += 1
        row["invoice_ids"].append(str(invoice.pk))
    currencies = []
    for currency in sorted(by_currency):
        rows = by_currency[currency]
        total = sum((r["amount"] for r in rows.values()), Decimal(0))
        currencies.append({
            "currency": currency, "total": _money(total), "count": sum(r["count"] for r in rows.values()),
            "overdue": _money(total - rows["current"]["amount"]),
            "buckets": [{"key": key, "label": label, "amount": _money(rows[key]["amount"]), "count": rows[key]["count"],
                         "invoice_ids": rows[key]["invoice_ids"]} for key, label in BUCKETS],
        })
    return {"as_of": today.isoformat(), "currencies": currencies,
            "note": "Unpaid invoices still in the sheet, by days past the due date (local business date)."}


# DSO and CEI -------------------------------------------------------------------------------------


def _period(now: dt.datetime) -> tuple[dt.datetime, dt.datetime, int]:
    days = settings.ANALYTICS_PERIOD_DAYS
    return now - dt.timedelta(days=days), now, days


def _history_problem(start: dt.datetime, seen: dict[Any, dt.datetime]) -> str:
    if not seen:
        return "No invoice has been imported yet."
    tracking = min(seen.values())
    if tracking > start:
        available = timezone.localdate(tracking + dt.timedelta(days=settings.ANALYTICS_PERIOD_DAYS))
        return (f"CollectFlow has seen invoices only since {timezone.localdate(tracking).isoformat()}; the "
                f"{settings.ANALYTICS_PERIOD_DAYS}-day period needs data from its start. Available from "
                f"{available.isoformat()}. Opening balances are never estimated.")
    return ""


def dso_and_cei(now: dt.datetime) -> dict[str, Any]:
    """
    Over the last ANALYTICS_PERIOD_DAYS (start S, end E = now), per currency:
      billed        = invoices first seen in [S, E)
      opening AR    = invoices first seen before S and not seen PAID before S
      closing AR    = invoices unpaid now; closing current AR = those not yet due at E
      DSO = closing AR / billed x period days
      CEI = (opening AR + billed - closing AR) / (opening AR + billed - closing current AR) x 100
    Reported only when CollectFlow saw invoices before S (otherwise the first import would count as
    billing and opening AR would be invented).
    """
    start, end, days = _period(now)
    seen, paid = first_seen(), first_paid_seen()
    problem = _history_problem(start, seen)
    today = timezone.localdate(end)
    sums: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    excluded = 0
    for invoice in Invoice.objects.all():
        if not invoice.in_sheet:
            excluded += 1
            continue
        first = seen.get(invoice.pk)
        if first is None:
            excluded += 1
            continue
        s = sums[invoice.currency]
        paid_at = paid.get(invoice.pk)
        if first < start and not (paid_at is not None and paid_at < start):
            s["opening"] += invoice.amount
        if start <= first < end:
            s["billed"] += invoice.amount
        if invoice.status == Invoice.Status.UNPAID:
            s["closing"] += invoice.amount
            if invoice.due_date >= today:
                s["closing_current"] += invoice.amount
    currencies = []
    for currency in sorted(sums):
        s = sums[currency]
        dso = cei = None
        dso_note = cei_note = problem
        if not problem:
            if s["billed"] > 0:
                dso = str((s["closing"] / s["billed"] * days).quantize(Decimal("0.1"), ROUND_HALF_UP))
            else:
                dso_note = "Nothing was billed in the period, so DSO is undefined."
            denominator = s["opening"] + s["billed"] - s["closing_current"]
            if denominator > 0:
                cei = str(((s["opening"] + s["billed"] - s["closing"]) / denominator * 100).quantize(Decimal("0.1"), ROUND_HALF_UP))
            else:
                cei_note = "No receivable was collectible in the period, so CEI is undefined."
        currencies.append({
            "currency": currency, "dso_days": dso, "dso_note": dso_note if dso is None else "",
            "cei_percent": cei, "cei_note": cei_note if cei is None else "",
            "inputs": {"opening_ar": _money(s["opening"]), "billed": _money(s["billed"]), "closing_ar": _money(s["closing"]),
                       "closing_current_ar": _money(s["closing_current"])},
        })
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(), "period_days": days,
        "currencies": currencies, "status": "insufficient_history" if problem else "ok", "reason": problem,
        "excluded_invoices": excluded,
        "note": "Billed = first seen by CollectFlow (the sheet has no invoice date); paid = first sync showing PAID.",
    }


# Reminder-to-payment attribution -----------------------------------------------------------------


def _sends_by_invoice() -> dict[Any, list[FollowUp]]:
    sends: dict[Any, list[FollowUp]] = defaultdict(list)
    for follow_up in FollowUp.objects.filter(status=FollowUp.Status.SENT, sent_at__isnull=False).prefetch_related(
        "statement_lines"
    ).select_related("step"):
        covered = ([line.invoice_id for line in follow_up.statement_lines.all()]
                   if follow_up.kind == FollowUp.Kind.STATEMENT else [follow_up.invoice_id])
        for invoice_id in covered:
            sends[invoice_id].append(follow_up)
    return sends


def attribution(now: dt.datetime) -> dict[str, Any]:
    """
    For each payment seen in the last ATTRIBUTION_LOOKBACK_DAYS: the latest reminder or statement sent
    about the invoice before the payment was seen. Within ATTRIBUTION_WINDOW_DAYS it counts as
    "paid after a reminder". This is a time association, not proof that the reminder caused it.
    """
    since = now - dt.timedelta(days=settings.ATTRIBUTION_LOOKBACK_DAYS)
    window = dt.timedelta(days=settings.ATTRIBUTION_WINDOW_DAYS)
    paid = first_paid_seen()
    sends = _sends_by_invoice()
    payments, groups = [], defaultdict(lambda: {"count": 0, "amount": Decimal(0)})
    unmeasured = Invoice.objects.filter(status=Invoice.Status.PAID).exclude(pk__in=list(paid)).count()
    for invoice in Invoice.objects.filter(pk__in=[k for k, at in paid.items() if at >= since]).order_by("external_id"):
        if invoice.status != Invoice.Status.PAID:
            continue
        seen_at = paid[invoice.pk]
        before = [f for f in sends.get(invoice.pk, []) if f.sent_at < seen_at]  # type: ignore[operator]
        last = max(before, key=lambda f: f.sent_at) if before else None  # type: ignore[arg-type,return-value]
        gap = (seen_at - last.sent_at) if last else None  # type: ignore[operator]
        after_reminder = gap is not None and gap <= window
        label = "none"
        if after_reminder and last is not None:
            label = "statement" if last.kind == FollowUp.Kind.STATEMENT else (
                f"step {last.step.order} ({last.tone.lower()})" if last.step else last.tone.lower())
        key = (invoice.currency, label)
        groups[key]["count"] += 1
        groups[key]["amount"] += invoice.amount
        payments.append({
            "invoice_id": str(invoice.pk), "external_id": invoice.external_id, "currency": invoice.currency,
            "amount": str(invoice.amount), "paid_seen_at": seen_at.isoformat(),
            "last_reminder_at": last.sent_at.isoformat() if last and last.sent_at else None,
            "last_reminder": label if after_reminder else None,
            "days_after_reminder": round(gap.total_seconds() / 86400, 1) if gap is not None else None,
            "after_reminder": after_reminder, "link": f"/?invoice={invoice.pk}",
        })
    summary = [{"currency": c, "reminder": label, "count": v["count"], "amount": _money(v["amount"])}
               for (c, label), v in sorted(groups.items())]
    return {
        "lookback_days": settings.ATTRIBUTION_LOOKBACK_DAYS, "window_days": settings.ATTRIBUTION_WINDOW_DAYS,
        "payments": payments, "summary": summary, "unmeasured_paid_invoices": unmeasured,
        "note": ("Association in time only: a payment seen within the window after a reminder. It does not prove "
                 "the reminder caused it. Paid = first sync showing PAID, so it lags the real payment."),
    }


# 30-day forecast ---------------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Decimal | None:
    return Decimal(numerator) / Decimal(denominator) if denominator else None


def forecast(now: dt.datetime) -> dict[str, Any]:
    """
    Expected cash per invoice = outstanding x P(paid within FORECAST_DAYS) (design §7H):
      open dispute                      -> P = 0 (never counted)
      open promise due in the window    -> P = the client's promise-kept rate (fewer than
                                           FORECAST_MIN_HISTORY settled promises: portfolio rate;
                                           none at all: FORECAST_PROMISE_DEFAULT), on the promised date
      otherwise                         -> P = FORECAST_BUCKET_P[aging bucket], on due date + the client's
                                           average days late (fewer than FORECAST_MIN_HISTORY payments:
                                           portfolio average; none: 0), but not before today (or, if
                                           FORECAST_OVERDUE_LAG_DAYS is set, today + the bucket's lag).
                                           Expected after the window: P x FORECAST_BEYOND_FACTOR, in the
                                           last week
      a promise broken in the last 180 days -> P x FORECAST_BROKEN_PROMISE_FACTOR
    An unconfirmed payment claim is not cash: it changes nothing. An estimate, not guaranteed cash.
    """
    today = timezone.localdate(now)
    end = today + dt.timedelta(days=settings.FORECAST_DAYS)
    min_history = settings.FORECAST_MIN_HISTORY
    paid = first_paid_seen()

    # Payment behaviour: days late per measured payment, by client and for the portfolio.
    late: dict[Any, list[int]] = defaultdict(list)
    for invoice in Invoice.objects.filter(status=Invoice.Status.PAID, pk__in=list(paid)):
        late[invoice.client_id].append(max((timezone.localdate(paid[invoice.pk]) - invoice.due_date).days, 0))
    portfolio_late = [d for days in late.values() for d in days]
    portfolio_avg = Decimal(sum(portfolio_late)) / len(portfolio_late) if portfolio_late else None

    settled: dict[Any, list[str]] = defaultdict(list)
    for client_id, status in PromiseToPay.objects.filter(status__in=["KEPT", "BROKEN"]).values_list("client_id", "status"):
        settled[client_id].append(status)
    all_settled = [s for statuses in settled.values() for s in statuses]
    portfolio_kept = _rate(all_settled.count("KEPT"), len(all_settled))

    disputed = set(DisputeCase.objects.filter(status=DisputeCase.Status.OPEN).values_list("invoice_id", flat=True))
    promises = {p.invoice_id: p for p in PromiseToPay.objects.filter(status=PromiseToPay.Status.OPEN)}
    broken_since = now - dt.timedelta(days=settings.RISK_BROKEN_PROMISE_WINDOW_DAYS)
    broken = set(PromiseToPay.objects.filter(status=PromiseToPay.Status.BROKEN, resolved_at__gte=broken_since)
                 .values_list("invoice_id", flat=True))
    claimed = set(ChaseHold.objects.filter(released_at__isnull=True, reason=ChaseHold.Reason.CLAIMS_PAID)
                  .values_list("invoice_id", flat=True))
    bucket_p = {key: Decimal(str(value)) for key, value in settings.FORECAST_BUCKET_P.items()}

    weeks = [(today + dt.timedelta(days=7 * n), min(today + dt.timedelta(days=7 * n + 6), end)) for n in range(5)]
    per_currency: dict[str, dict[str, Any]] = {}
    rows = []
    for invoice in _outstanding():
        cur = per_currency.setdefault(invoice.currency, {
            "outstanding": Decimal(0), "expected": Decimal(0), "promise_expected": Decimal(0), "excluded": Decimal(0),
            "weeks": [Decimal(0) for _ in weeks]})
        cur["outstanding"] += invoice.amount
        days_overdue = (today - invoice.due_date).days
        bucket = bucket_of(days_overdue)
        flags: list[str] = []
        low_confidence = False
        promise = promises.get(invoice.pk)
        if invoice.pk in disputed:
            p, when, source = Decimal(0), None, "dispute"
            flags.append("Open dispute: not counted.")
        elif promise is not None and promise.promised_date <= end:
            history = settled.get(invoice.client_id, [])
            if len(history) >= min_history:
                p = _rate(history.count("KEPT"), len(history))  # type: ignore[assignment]
                flags.append(f"Promise on {promise.promised_date.isoformat()}: client kept {history.count('KEPT')} of {len(history)}.")
            elif portfolio_kept is not None:
                p, low_confidence = portfolio_kept, True
                flags.append(f"Promise on {promise.promised_date.isoformat()}: portfolio promise-kept rate (client history too short).")
            else:
                p, low_confidence = Decimal(str(settings.FORECAST_PROMISE_DEFAULT)), True
                flags.append(f"Promise on {promise.promised_date.isoformat()}: no promise history yet, default rate.")
            when, source = max(promise.promised_date, today), "promise"
        else:
            history_late = late.get(invoice.client_id, [])
            if len(history_late) >= min_history:
                avg = Decimal(sum(history_late)) / len(history_late)
            else:
                low_confidence = True
                avg = portfolio_avg if portfolio_avg is not None else Decimal(0)
            p, source = bucket_p[bucket], "aging"
            expected = invoice.due_date + dt.timedelta(days=int(avg.to_integral_value(ROUND_HALF_UP)))
            when = max(expected, today)
            lag = (settings.FORECAST_OVERDUE_LAG_DAYS or {}).get(bucket)
            if expected < today and lag:
                # M15 proposal, off unless configured: an invoice already past its expected date is placed
                # `lag` days ahead (by aging bucket) instead of all on today, which piled them into week 1.
                when = today + dt.timedelta(days=int(lag))
                flags.append(f"Already past its expected date: assumed in about {lag} days (aging bucket {bucket}).")
            if when > end:
                p = p * Decimal(str(settings.FORECAST_BEYOND_FACTOR))
                flags.append(f"Expected around {when.isoformat()}, after the window: reduced chance.")
                when = weeks[-1][0]
            flags.append(f"Aging bucket {bucket}; average days late used: {avg.quantize(Decimal('0.1'))}"
                         + (" (portfolio, low confidence)." if low_confidence else "."))
        if source != "dispute" and invoice.pk in broken:
            p = p * Decimal(str(settings.FORECAST_BROKEN_PROMISE_FACTOR))
            flags.append("A promise on this invoice was broken recently: reduced chance.")
        if invoice.pk in claimed:
            flags.append("The client says it is paid, but the sheet does not show it: not treated as paid.")
        p = p.quantize(Decimal("0.0001"), ROUND_HALF_UP)
        expected = (invoice.amount * p).quantize(CENT, ROUND_HALF_UP)
        cur["expected"] += expected
        if source == "promise":
            cur["promise_expected"] += expected
        if source == "dispute":
            cur["excluded"] += invoice.amount
        if when is not None:
            week = next(n for n, (first, last) in enumerate(weeks) if first <= when <= last) if when <= end else len(weeks) - 1
            cur["weeks"][week] += expected
        rows.append({
            "invoice_id": str(invoice.pk), "external_id": invoice.external_id, "client_name": invoice.client_name,
            "currency": invoice.currency, "amount": str(invoice.amount), "probability": str(p),
            "expected": str(expected), "expected_date": when.isoformat() if when else None, "source": source,
            "bucket": bucket, "low_confidence": low_confidence, "explanation": flags, "link": f"/?invoice={invoice.pk}",
        })
    return {
        "as_of": today.isoformat(), "window_end": end.isoformat(), "days": settings.FORECAST_DAYS,
        "weeks": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in weeks],
        "currencies": [{
            "currency": currency, "outstanding": _money(v["outstanding"]), "expected": _money(v["expected"]),
            "promise_expected": _money(v["promise_expected"]), "excluded_disputed": _money(v["excluded"]),
            "weeks": [_money(w) for w in v["weeks"]],
        } for currency, v in sorted(per_currency.items())],
        "invoices": rows,
        "assumptions": {
            "bucket_probabilities": {k: str(v) for k, v in bucket_p.items()},
            "beyond_window_factor": str(settings.FORECAST_BEYOND_FACTOR),
            "broken_promise_factor": str(settings.FORECAST_BROKEN_PROMISE_FACTOR),
            "promise_default_rate": str(settings.FORECAST_PROMISE_DEFAULT),
            "min_history": min_history,
            "overdue_lag_days": settings.FORECAST_OVERDUE_LAG_DAYS or None,
            "portfolio_avg_days_late": str(portfolio_avg.quantize(Decimal("0.1"))) if portfolio_avg is not None else None,
            "portfolio_promise_kept_rate": str(portfolio_kept.quantize(Decimal("0.0001"))) if portfolio_kept is not None else None,
        },
        "note": "An estimate, not guaranteed cash. Disputed invoices and unconfirmed payment claims are never counted as paid.",
    }


# Summary strip and snapshots ---------------------------------------------------------------------


def kpis(aging_data: dict[str, Any], dso_data: dict[str, Any], forecast_data: dict[str, Any]) -> dict[str, Any]:
    """The design's KPI strip, per currency: overdue amount, expected cash in 30 days, DSO, open promises."""
    promises = defaultdict(lambda: {"count": 0, "amount": Decimal(0)})
    for p in PromiseToPay.objects.filter(status=PromiseToPay.Status.OPEN):
        promises[p.currency]["count"] += 1
        promises[p.currency]["amount"] += p.amount
    currencies = sorted({c["currency"] for c in aging_data["currencies"]} | set(promises))
    by = lambda data, key: {c["currency"]: c for c in data.get(key, [])}  # noqa: E731
    aging_by, dso_by, forecast_by = by(aging_data, "currencies"), by(dso_data, "currencies"), by(forecast_data, "currencies")
    return {"currencies": [{
        "currency": c,
        "overdue": aging_by.get(c, {}).get("overdue", "0.00"),
        "expected_30_days": forecast_by.get(c, {}).get("expected", "0.00"),
        "dso_days": dso_by.get(c, {}).get("dso_days"),
        "dso_note": dso_by.get(c, {}).get("dso_note", dso_data.get("reason", "")),
        "open_promises": promises[c]["count"] if c in promises else 0,
        "open_promise_amount": _money(promises[c]["amount"]) if c in promises else "0.00",
    } for c in currencies]}


def compute(now: dt.datetime | None = None) -> dict[str, dict[str, Any]]:
    now = now or timezone.now()
    data = {"aging": aging(now), "dso_cei": dso_and_cei(now), "attribution": attribution(now), "forecast": forecast(now)}
    data["kpis"] = kpis(data["aging"], data["dso_cei"], data["forecast"])
    return data


def refresh(now: dt.datetime | None = None) -> dict[str, int]:
    """Recomputes and stores every snapshot (one row per kind, replaced). Idempotent."""
    now = now or timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:  # serialized with risk recomputes
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ADVISORY_LOCK_KEY])
        data = compute(now)
        for kind, payload in data.items():
            AnalyticsSnapshot.objects.update_or_create(kind=kind, defaults={"data": payload, "computed_at": now})
    return {"snapshots": len(data)}
