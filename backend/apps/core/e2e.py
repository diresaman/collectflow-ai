"""
Browser end-to-end test support (M15). Not used by the application.

`install_fakes()` replaces the Gemini and Gmail clients with deterministic fakes, and `seed()` writes one
fictional record set per user journey. Both refuse to run unless the database name ends with "_e2e" and
DEBUG is on, so they can never touch the development or real database or reach a provider.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

PASSWORD = "e2e-pass-123"
UNKNOWN_RECIPIENT_PREFIX = "unknown"


class E2EGuardError(RuntimeError):
    pass


def require_e2e_database() -> None:
    name = str(settings.DATABASES["default"]["NAME"])
    if not name.endswith("_e2e") or not settings.DEBUG:
        raise E2EGuardError(f"Refusing: e2e support needs a database ending in _e2e and DEBUG on (got {name!r}).")


class FakeGemini:
    """Answers by response schema: statement wording, reminder drafts, reply classification."""

    model = "e2e-fake-gemini"

    def generate_json(self, *, response_schema: dict[str, Any], **_: Any) -> dict[str, Any]:
        props = response_schema.get("properties", {})
        if "opening" in props:
            return {"subject": "Statement of account: outstanding invoices",
                    "opening": "Hello, here is a summary of the invoices that are still open on your account.",
                    "closing": "Please arrange payment, or reply if anything looks wrong.\n\nAccounts Receivable Team",
                    "reasoning": "Several overdue invoices for one client."}
        if "tone" in props:
            tone = props["tone"]["enum"][0]
            return {"subject": f"{tone.title()} reminder about your invoice",
                    "body": "Hello, this is a reminder that your invoice is past due. Accounts Receivable Team",
                    "tone": tone, "reasoning": "Sequence step.", "recommended_action": "Review and approve."}
        return {"label": "OTHER", "confidence": 0.5, "suspicious_instructions": False, "rationale": "e2e fake",
                "extracted": {}}


class FakeGmail:
    """Accepts every send, except to recipients starting with "unknown" (outcome unknown)."""

    sender_email = "ar@e2e.test"

    def check_configured(self) -> Any:
        from integrations.gmail import GmailStatus

        return GmailStatus(True, True, self.sender_email, None, "Gmail is configured (e2e fake).")

    def check_ready(self) -> Any:
        from integrations.gmail import GmailStatus

        return GmailStatus(True, True, self.sender_email, self.sender_email, "Gmail is ready (e2e fake).")

    def send(self, *, recipient: str, message_id: str = "", **_: Any) -> Any:
        from integrations.gmail import DeliveryResult, GmailDeliveryUnknown

        if recipient.startswith(UNKNOWN_RECIPIENT_PREFIX):
            raise GmailDeliveryUnknown("e2e fake: no answer from Gmail")
        return DeliveryResult(message_id=f"e2e-{message_id.strip('<>')[:40]}", thread_id="e2e-thread")


def install_fakes() -> None:
    require_e2e_database()
    from integrations.gemini import GeminiClient
    from integrations.gmail import GmailClient

    GeminiClient.from_settings = classmethod(lambda cls: FakeGemini())  # type: ignore[method-assign,assignment]
    GmailClient.from_settings = classmethod(lambda cls: FakeGmail())  # type: ignore[method-assign,assignment]


def _user(username: str, role: str, first_name: str) -> Any:
    from apps.accounts.models import Profile

    user = get_user_model().objects.create_user(username, password=PASSWORD, first_name=first_name)
    Profile.objects.create(user=user, role=role)
    return user


def _invoice(external_id: str, client: str, email: str, *, days: int, amount: str, **fields: Any) -> Any:
    from apps.invoices.models import Invoice

    return Invoice.objects.create(
        external_id=external_id, client_name=client, client_email=email, amount=Decimal(amount), currency="USD",
        due_date=timezone.localdate() - dt.timedelta(days=days), status=Invoice.Status.UNPAID,
        last_synced_at=timezone.now(), **fields)


def seed() -> dict[str, Any]:
    """Fictional data, one set per journey (see frontend/e2e/README.md). Call on an empty, migrated database."""
    require_e2e_database()
    from apps.accounts.models import Role
    from apps.cases import disputes, promises
    from apps.cases.models import PromiseToPay
    from apps.cases.tests.helpers import chased, reply
    from apps.followups import statements
    from apps.followups.models import DeliveryAttempt, FollowUp
    from apps.followups.tests.helpers import make_follow_up
    from apps.inbox import services as tasks
    from apps.insights import analytics, risk
    from apps.invoices.models import Invoice
    from apps.sequences import engine
    from apps.timeline.models import ActorType

    operator = _user("e2e-operator", Role.OPERATOR, "Olga")
    _user("e2e-approver", Role.APPROVER, "Abel")
    _user("e2e-approver2", Role.APPROVER, "Amara")

    # J3 risk: one client with several overdue invoices, a broken promise and unanswered reminders.
    harbor = [_invoice(f"HA-10{n}", "Harbor Analytics", "ap.harbor@e2e.test", days=d, amount=a)
              for n, (d, a) in enumerate([(70, "2200.00"), (40, "800.00")], 1)]
    for days_ago in (12, 6):
        make_follow_up(harbor[0], FollowUp.Status.SENT, sent_at=timezone.now() - dt.timedelta(days=days_ago),
                       subject="Reminder: invoice HA-101")
    broken = promises.record(harbor[1].pk, promised_date=timezone.localdate() + dt.timedelta(days=1), actor=operator)
    PromiseToPay.objects.filter(pk=broken.pk).update(status="BROKEN", resolved_at=timezone.now() - dt.timedelta(days=3))
    from apps.invoices.models import ChaseHold

    ChaseHold.objects.filter(promise_id=broken.pk).update(released_at=timezone.now())

    # J4 + J8 disputes: one to inspect, one to manage and resolve.
    for external_id in ("DW-200", "DW-201"):
        invoice = chased(external_id, f"thread-{external_id}", days=25)
        disputes.open_case(invoice.pk, reason="AMOUNT", description=f"Client disputes the hours on {external_id}.",
                           actor=operator)

    # J5 reply to classify, J9 promise reply to record.
    r1 = chased("RB-300", "thread-RB-300", days=15)
    reply(r1, "Hi, could you send us a copy of the timesheet for this invoice?", label="OTHER", confidence=0.41,
          thread="thread-RB-300")
    r2 = chased("RB-301", "thread-RB-301", days=18)
    reply(r2, "We will pay the full amount next Friday.", label="PROMISE_TO_PAY", confidence=0.97,
          thread="thread-RB-301", promised_date=(timezone.localdate() + dt.timedelta(days=5)).isoformat())

    # J6 edit and approve a friendly reminder, then send it (fake Gmail).
    elm = _invoice("EL-400", "Elm Studio", "billing.elm@e2e.test", days=6, amount="420.00")
    make_follow_up(elm, subject="Friendly reminder: invoice EL-400",
                   body="Hi Elm Studio,\n\nA quick reminder that invoice EL-400 is a few days past due.\n\nAccounts Receivable Team")

    # J7 four-eyes: a firm, high-value draft.
    falcon = _invoice("FA-500", "Falcon Logistics", "ap.falcon@e2e.test", days=35, amount="6500.00")
    make_follow_up(falcon, subject="Invoice FA-500 is now over a month overdue", tone="FIRM",
                   body="Hello Falcon Logistics,\n\nInvoice FA-500 is now more than a month overdue. Please arrange payment.\n\nAccounts Receivable Team")

    # J10 task to assign and complete.
    t = chased("TK-600", "thread-TK-600", days=12)
    tasks.create(t.pk, title="Call Tidewater about the missing purchase order",
                 due_at=(timezone.now() - dt.timedelta(hours=6)).isoformat(), actor=operator)

    # J12 statement: three overdue invoices, no reminders yet.
    for n, (days, amount) in enumerate([(14, "300.00"), (28, "950.00"), (43, "610.00")], 1):
        _invoice(f"SP-70{n}", "Summit Partners", "ap.summit@e2e.test", days=days, amount=amount)
    # Stale statement: prepared, then one invoice changed in the sheet.
    stale = [_invoice(f"ST-80{n}", "Stale Supplies", "ap.stale@e2e.test", days=20 + n, amount="200.00") for n in (1, 2)]
    from apps.invoices.models import Client

    ids = [line["invoice_id"] for line in statements.preview(Client.objects.get(email="ap.stale@e2e.test"))["lines"]]
    statements.generate(stale[0].client_id, ids, gemini=FakeGemini())
    Invoice.objects.filter(pk=stale[1].pk).update(amount=Decimal("260.00"))

    # J13 unknown delivery outcome.
    unknown = _invoice("UN-900", "Umbra Media", "unknown.umbra@e2e.test", days=22, amount="990.00")
    fu = make_follow_up(unknown, FollowUp.Status.DELIVERY_UNKNOWN, subject="Reminder: invoice UN-900")
    DeliveryAttempt.objects.create(follow_up=fu, status="DELIVERY_UNKNOWN", recipient_email=unknown.client_email,
                                   started_at=timezone.now() - dt.timedelta(hours=2), error_code="timeout",
                                   error_summary="No answer from Gmail; it may or may not have been sent.")

    engine.reconcile(actor=ActorType.SYSTEM)
    return {"risk": risk.recompute(), "analytics": analytics.refresh(), "invoices": Invoice.objects.count()}
