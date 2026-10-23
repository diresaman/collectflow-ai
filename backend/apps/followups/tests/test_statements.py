import datetime as dt
import threading
from decimal import Decimal
from typing import Any

from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.automation.models import OutboxJob
from apps.followups import services, statements
from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.eligibility import IN_STATEMENT, ineligibility
from apps.followups.models import DeliveryAttempt, FollowUp, GenerationAttempt, StatementLine
from apps.invoices.models import ChaseHold, Client, Invoice
from apps.sequences import engine
from apps.timeline.models import ActorType, EventType, TimelineEvent
from apps.timeline.testing import TimelineTransactionTestCase
from integrations.gmail import GmailDeliveryUnknown

from .helpers import FakeGmail, make_follow_up, overdue_invoice

EMAIL = "ap@client.test"
isolated = override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[], FOLLOWUP_COOLDOWN_DAYS=7)


class FakeStatementAI:
    model = "fake-gemini"

    def __init__(self, **overrides: str) -> None:
        self.response = {"subject": "Statement of account: outstanding invoices",
                         "opening": "Hello Client Co, here is a summary of the invoices that are still open.",
                         "closing": "Please arrange payment, or reply if anything looks wrong.\n\nAccounts Receivable Team",
                         "reasoning": "Several overdue invoices for one client."} | overrides
        self.calls: list[dict[str, Any]] = []

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.response


def invoices(n: int = 3, *, email: str = EMAIL, prefix: str = "INV", **overrides: Any) -> list[Invoice]:
    return [overdue_invoice(days=10 + 10 * i, external_id=f"{prefix}-{i + 1}", client_email=email, client_name="Client Co",
                            amount=Decimal(f"{(i + 1) * 100}.00"), **overrides) for i in range(n)]


def client(email: str = EMAIL) -> Client:
    return Client.objects.get(email=email)


def user(name: str, role: str = Role.OPERATOR) -> Any:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(username=name).first() or make_user(name, role)


def prepare(ai: FakeStatementAI | None = None, email: str = EMAIL) -> FollowUp:
    ids = [line["invoice_id"] for line in statements.preview(client(email))["lines"]]
    return statements.generate(client(email).pk, ids, gemini=ai or FakeStatementAI())


def approve(statement: FollowUp) -> FollowUp:
    return services.approve(statement.pk, actor=user("boss", Role.APPROVER))


@isolated
class EligibilityTests(TestCase):
    def test_needs_two_chaseable_invoices_and_explains_the_rest(self) -> None:
        a, b = invoices(2)
        overdue_invoice(days=-3, external_id="INV-NOT-DUE", client_email=EMAIL)
        overdue_invoice(days=40, external_id="INV-PAID", client_email=EMAIL, status=Invoice.Status.PAID)
        make_follow_up(b, FollowUp.Status.APPROVED)
        info = statements.preview(client())
        self.assertFalse(info["can_prepare"])
        self.assertEqual([line["external_id"] for line in info["lines"]], ["INV-1"])
        excluded = {e["external_id"]: e["reasons"] for e in info["excluded"]}
        self.assertIn("INV-NOT-DUE", excluded)  # paid invoices are not listed at all
        self.assertNotIn("INV-PAID", excluded)
        self.assertIn("approved", excluded["INV-2"][0])
        with self.assertRaises(services.NotEligible):
            statements.request(client().pk, actor=make_user())

    def test_holds_disputes_and_cooldown_exclude_invoices(self) -> None:
        a, b, c = invoices(3)
        ChaseHold.objects.create(invoice=a, reason=ChaseHold.Reason.NEEDS_REVIEW, reply_id=_reply(a).pk)
        make_follow_up(b, FollowUp.Status.SENT, sent_at=timezone.now() - dt.timedelta(days=2))
        info = statements.preview(client())
        excluded = {e["external_id"]: " ".join(e["reasons"]) for e in info["excluded"]}
        self.assertIn("on hold", excluded["INV-1"])
        self.assertIn("cooldown", excluded["INV-2"])
        self.assertEqual([line["external_id"] for line in info["lines"]], ["INV-3"])

    def test_request_queues_one_job(self) -> None:
        invoices(3)
        job = statements.request(client().pk, actor=make_user())
        self.assertEqual(job.kind, statements.STATEMENT_JOB_KIND)
        self.assertEqual(len(job.payload["invoice_ids"]), 3)
        with self.assertRaises(services.GenerationInProgress):
            statements.request(client().pk, actor=make_user("other"))


def _reply(invoice: Invoice) -> Any:
    from apps.inbox.models import InboundMessage

    return InboundMessage.objects.create(gmail_message_id=f"m-{invoice.pk}", gmail_thread_id="t", invoice=invoice,
                                         client=invoice.client, sender_email=EMAIL, received_at=timezone.now(),
                                         state=InboundMessage.State.TRIAGE)


@isolated
class GenerationTests(TestCase):
    def test_three_invoices_three_lines_with_a_backend_total(self) -> None:
        # Design acceptance: client with 3 overdue invoices -> statement lists 3 lines, backend total.
        invoices(3)
        ai = FakeStatementAI()
        statement = prepare(ai)
        self.assertEqual(statement.kind, FollowUp.Kind.STATEMENT)
        self.assertEqual(statement.status, FollowUp.Status.DRAFT)
        lines = statements.lines_of(statement)
        self.assertEqual([line.invoice.external_id for line in lines], ["INV-3", "INV-2", "INV-1"])  # oldest due first
        self.assertIn("Total outstanding (USD): 600.00 USD", statement.body)
        self.assertIn("- INV-3: 300.00 USD, due", statement.body)
        prompt = ai.calls[0]["prompt"]
        self.assertNotIn("300", prompt)  # the AI never receives amounts
        self.assertNotIn(EMAIL, prompt)
        attempt = GenerationAttempt.objects.get()
        self.assertEqual((attempt.purpose, attempt.outcome), ("STATEMENT", "SUCCEEDED"))
        self.assertEqual(TimelineEvent.objects.filter(type=EventType.DRAFT_GENERATED, payload__kind="STATEMENT").count(), 3)

    def test_currencies_get_separate_totals(self) -> None:
        invoices(2)
        overdue_invoice(days=15, external_id="INV-EUR", client_email=EMAIL, amount=Decimal("50.00"), currency="EUR")
        statement = prepare()
        self.assertIn("Total outstanding (EUR): 50.00 EUR", statement.body)
        self.assertIn("Total outstanding (USD): 300.00 USD", statement.body)
        self.assertEqual(statements.totals(statements.lines_of(statement)), {"EUR": Decimal("50.00"), "USD": Decimal("300.00")})

    def test_ai_figures_and_links_are_rejected(self) -> None:
        invoices(2)
        for bad in ({"opening": "You owe 300.00 in total."}, {"closing": "Pay USD 300 now."},
                    {"opening": "Pay at https://pay.example"}):
            with self.subTest(bad=bad), self.assertRaises(services.AIBadResponse):
                prepare(FakeStatementAI(**bad))
        self.assertFalse(FollowUp.objects.filter(kind=FollowUp.Kind.STATEMENT).exists())
        self.assertEqual(GenerationAttempt.objects.filter(outcome="FAILED", purpose="STATEMENT").count(), 3)

    def test_replaces_unsent_drafts_and_locks_the_invoices(self) -> None:
        a, b = invoices(2)
        draft = make_follow_up(a)
        statement = prepare()
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.cancelled_reason), ("REJECTED", "statement"))
        self.assertIn(IN_STATEMENT, [code for code, _ in ineligibility(b)])
        self.assertEqual(ineligibility(b, statement=statement.pk), [])
        with self.assertRaises(services.NotEligible):
            services.check_can_generate(b, regenerate=False)

    def test_eligibility_changed_since_the_request(self) -> None:
        a, b, c = invoices(3)
        ids = [str(i.pk) for i in (a, b, c)]
        Invoice.objects.filter(pk=c.pk).update(status=Invoice.Status.PAID)
        ai = FakeStatementAI()
        with self.assertRaises(services.StaleInvoice):
            statements.generate(client().pk, ids, gemini=ai)
        self.assertEqual(ai.calls, [])  # refused before any AI call
        self.assertFalse(GenerationAttempt.objects.exists())


@isolated
class ReviewAndApprovalTests(TestCase):
    def test_always_an_approver_who_did_not_edit_it(self) -> None:
        invoices(2)
        statement = prepare()
        operator, editor, other = make_user("op"), make_user("ed", Role.APPROVER), make_user("ap2", Role.APPROVER)
        with self.assertRaises(services.ApprovalForbidden) as refused:
            services.approve(statement.pk, actor=operator)
        self.assertEqual(refused.exception.code, "approver_required")
        services.update_draft(statement.pk, {"body": statement.body.replace("Hello", "Good morning")}, actor=editor)
        with self.assertRaises(services.ApprovalForbidden) as refused:
            services.approve(statement.pk, actor=editor)
        self.assertEqual(refused.exception.code, "self_approval")
        self.assertEqual(services.approve(statement.pk, actor=other).status, FollowUp.Status.APPROVED)

    def test_the_invoice_lines_cannot_be_edited(self) -> None:
        invoices(2)
        statement = prepare()
        with self.assertRaises(services.InvalidContent):
            services.update_draft(statement.pk, {"body": statement.body.replace("200.00", "20.00")}, actor=make_user())

    def test_approval_refused_when_a_line_changed(self) -> None:
        a, _ = invoices(2)
        statement = prepare()
        Invoice.objects.filter(pk=a.pk).update(amount=Decimal("999.00"))
        with self.assertRaises(services.StaleInvoice):
            approve(statement)


@isolated
class DeliveryTests(TestCase):
    def send(self, statement: FollowUp, gmail: FakeGmail | None = None) -> Any:
        return FollowUpDeliveryService(gmail=gmail or FakeGmail()).send(statement.pk, actor=user("sender"))

    def test_sends_one_email_listing_every_invoice(self) -> None:
        invoices(3)
        statement = approve(prepare())
        gmail = FakeGmail()
        outcome = self.send(statement, gmail)
        self.assertEqual(outcome.follow_up.status, FollowUp.Status.SENT)
        self.assertEqual(len(gmail.sent), 1)
        self.assertEqual(gmail.sent[0]["recipient"], EMAIL)
        self.assertIn("Total outstanding (USD): 600.00 USD", gmail.sent[0]["body"])
        self.assertEqual(TimelineEvent.objects.filter(type=EventType.FOLLOW_UP_SENT).count(), 3)  # one per invoice
        # Lines are free again, and the statement counts as the last reminder for each (cooldown).
        for line in statements.lines_of(outcome.follow_up):
            self.assertNotIn(IN_STATEMENT, [code for code, _ in ineligibility(line.invoice)])
            self.assertEqual(engine.last_sent_at(line.invoice), outcome.follow_up.sent_at)
        with self.assertRaises(services.FollowUpError):
            self.send(outcome.follow_up)  # never twice

    def test_invoice_paid_held_or_changed_after_approval_blocks_the_send(self) -> None:
        for change in ("paid", "hold", "amount"):
            with self.subTest(change=change):
                email = f"{change}@client.test"
                fresh = invoices(2, email=email, prefix=change)
                statement = approve(prepare(email=email))
                target = fresh[1]
                if change == "paid":
                    Invoice.objects.filter(pk=target.pk).update(status=Invoice.Status.PAID)
                elif change == "hold":
                    ChaseHold.objects.create(invoice=target, reason=ChaseHold.Reason.NEEDS_REVIEW, reply_id=_reply(target).pk)
                else:
                    Invoice.objects.filter(pk=target.pk).update(amount=Decimal("1.00"))
                gmail = FakeGmail()
                with self.assertRaises(services.FollowUpError):
                    self.send(statement, gmail)
                self.assertEqual(gmail.sent, [])
                statement.refresh_from_db()
                self.assertEqual(statement.status, FollowUp.Status.APPROVED)  # nothing reserved

    def test_a_queued_delivery_rechecks_before_gmail(self) -> None:
        a, _ = invoices(2)
        statement = approve(prepare())
        service = FollowUpDeliveryService(gmail=FakeGmail())
        attempt = service.request(statement.pk, actor=user("sender"), enqueue=False)
        Invoice.objects.filter(pk=a.pk).update(status=Invoice.Status.PAID)  # the sheet changed while queued
        gmail = FakeGmail()
        with self.assertRaises(services.FollowUpError):
            FollowUpDeliveryService(gmail=gmail).deliver(attempt.pk)
        self.assertEqual(gmail.sent, [])
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, DeliveryAttempt.Status.FAILED)

    def test_unknown_outcome_is_never_retried(self) -> None:
        a, _ = invoices(2)
        statement = approve(prepare())
        gmail = FakeGmail(error=GmailDeliveryUnknown("timeout"))
        outcome = self.send(statement, gmail)
        self.assertEqual(outcome.follow_up.status, FollowUp.Status.DELIVERY_UNKNOWN)
        with self.assertRaises(services.FollowUpError):
            self.send(outcome.follow_up, gmail)
        self.assertEqual(len(gmail.sent), 1)
        self.assertIn(IN_STATEMENT, [code for code, _ in ineligibility(a)])  # still locked until reconciled
        self.assertFalse(OutboxJob.objects.filter(kind="followups.deliver").exists())

    def test_a_dispute_on_one_line_withdraws_the_statement(self) -> None:
        from apps.cases import disputes

        a, _ = invoices(2)
        statement = approve(prepare())
        disputes.open_case(a.pk, reason="AMOUNT", description="x", actor=make_user("op"))
        statement.refresh_from_db()
        self.assertEqual((statement.status, statement.cancelled_reason), ("REJECTED", "dispute"))


@isolated
class StatementReplyTests(TestCase):
    def test_a_reply_to_a_statement_holds_every_listed_invoice(self) -> None:
        from apps.inbox import ingestion
        from apps.inbox.models import InboundMessage, MailboxCursor
        from apps.inbox.tests.helpers import FakeMailbox, mail

        invoices(2)
        statement = approve(prepare())
        FollowUpDeliveryService(gmail=FakeGmail()).send(statement.pk, actor=user("sender"))
        MailboxCursor.objects.create(name="gmail", history_id="100")
        box = FakeMailbox()
        box.add(mail("reply-1", "thread-456", sender=EMAIL, text="We disagree with one of these."))
        ingestion.poll(box, owner="test")  # type: ignore[arg-type]
        reply = InboundMessage.objects.get()
        self.assertEqual(len(reply.thread_invoice_ids), 2)
        self.assertEqual(ChaseHold.objects.filter(released_at__isnull=True).values("invoice_id").distinct().count(), 2)


class ConcurrentSendTests(TimelineTransactionTestCase):
    """PostgreSQL threads: three simultaneous send requests reserve one delivery."""

    @isolated
    def test_three_requests_one_attempt(self) -> None:
        invoices(2)
        statement = approve(prepare())
        results: list[str] = []
        barrier = threading.Barrier(3)

        def run() -> None:
            try:
                barrier.wait()
                FollowUpDeliveryService(gmail=FakeGmail()).request(statement.pk, actor=None, enqueue=False)
                results.append("reserved")
            except services.FollowUpError as error:
                results.append(error.code)
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(DeliveryAttempt.objects.count(), 1)


@isolated
class StatementApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def test_preview_request_and_roles(self) -> None:
        invoices(3)
        url = f"/api/clients/{client().pk}/statement/"
        preview = self.client.get(url).json()
        self.assertTrue(preview["can_prepare"])
        self.assertEqual(preview["totals"], {"USD": "600.00"})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.post(url).status_code, 409)  # already being prepared
        self.client.force_login(make_user("nobody", role=None))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_follow_up_api_shows_lines_and_the_approver_rule(self) -> None:
        a, b = invoices(2)
        statement = prepare()
        data = self.client.get(f"/api/follow-ups/?invoice={b.pk}").json()
        row = next(r for r in data if r["id"] == str(statement.pk))  # listed under every line invoice
        self.assertEqual(row["kind"], "STATEMENT")
        self.assertEqual(row["statement"]["totals"], {"USD": "300.00"})
        self.assertEqual(row["approval"]["required_role"], "APPROVER")
        self.assertIsNotNone(row["approval"]["blocked_for_you"])  # an operator cannot approve it
        del a


@isolated
class WorklistSuggestionTests(SessionApiTestCase):
    role = Role.APPROVER

    def test_the_worklist_suggests_reviewing_the_statement(self) -> None:
        from apps.insights import risk

        invoices(2)
        prepare()
        risk.recompute()
        row = self.client.get("/api/worklist/").json()["groups"][0]["rows"][0]
        self.assertEqual(row["next_action"]["code"], "drafts")
        self.assertIn(IN_STATEMENT, [b["code"] for b in row["blockers"]])


@isolated
class StatementReconciliationTests(TestCase):
    def test_an_unknown_statement_is_reconciled_on_every_invoice(self) -> None:
        from apps.followups.delivery import reconcile

        a, b = invoices(2)
        statement = approve(prepare())
        outcome = FollowUpDeliveryService(gmail=FakeGmail(error=GmailDeliveryUnknown("timeout"))).send(
            statement.pk, actor=user("sender"))
        self.assertEqual(outcome.follow_up.status, FollowUp.Status.DELIVERY_UNKNOWN)
        reconcile(statement.pk, outcome=FollowUp.Status.FAILED, note="not in Sent")
        events = TimelineEvent.objects.filter(type=EventType.DELIVERY_RECONCILED)
        self.assertEqual({e.invoice_id for e in events}, {a.pk, b.pk})
        self.assertNotIn(IN_STATEMENT, [code for code, _ in ineligibility(a)])  # the lock ends with the outcome
