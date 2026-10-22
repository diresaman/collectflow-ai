import json
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.cases import disputes, promises
from apps.cases.common import ApproverRequired, InvalidCase
from apps.cases.models import DisputeCase
from apps.followups import services as followups
from apps.followups.delivery import FollowUpDeliveryService
from apps.followups.models import FollowUp
from apps.followups.tests.helpers import FakeGmail, make_follow_up
from apps.inbox.models import Task
from apps.invoices.models import ChaseHold, Invoice
from apps.sequences import engine
from apps.sequences.models import SequenceEnrollment
from apps.sequences.tests.helpers import StepGemini
from apps.timeline.models import ActorType, TimelineEvent

from .helpers import chased, reply, today_plus, users


def active_holds(invoice) -> list[tuple[str, str]]:  # type: ignore[no-untyped-def]
    return sorted((h.source, h.reason) for h in ChaseHold.objects.filter(invoice=invoice, released_at__isnull=True))


class OpenDisputeTests(TestCase):
    def setUp(self) -> None:
        self.operator, self.approver = users()
        self.invoice = chased()

    def test_from_a_reply(self) -> None:
        message = reply(self.invoice, "The amount is wrong, we agreed 450.", label="DISPUTE")
        case = disputes.open_case(self.invoice.pk, reason="AMOUNT", description="Agreed 450, invoiced 600",
                                  reply_id=message.pk, owner_id=self.operator.pk, actor=self.operator)
        self.assertEqual((case.status, case.owner, case.opened_by), ("OPEN", self.operator, self.operator))
        self.assertEqual(active_holds(self.invoice), [("dispute", "DISPUTE")])
        review = Task.objects.get(dispute=case)
        self.assertEqual((review.kind, review.status), ("REVIEW_DISPUTE", "OPEN"))
        message.refresh_from_db()
        self.assertEqual((message.state, message.label, message.label_source), ("RESOLVED", "DISPUTE", "HUMAN"))
        # Handling the same reply again does not open a second case or task.
        again = disputes.open_case(self.invoice.pk, reason="AMOUNT", description="again", reply_id=message.pk,
                                   actor=self.operator)
        self.assertEqual(again.pk, case.pk)
        self.assertEqual((DisputeCase.objects.count(), Task.objects.filter(kind="REVIEW_DISPUTE").count()), (1, 1))

    def test_validation_and_one_open_case(self) -> None:
        for kwargs in ({"reason": "MAYBE", "description": "x"}, {"reason": "AMOUNT", "description": ""},
                       {"reason": "AMOUNT", "description": "x", "owner_id": 999999}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidCase):
                disputes.open_case(self.invoice.pk, actor=self.operator, **kwargs)
        disputes.open_case(self.invoice.pk, reason="QUALITY", description="Poor work", actor=self.operator)
        with self.assertRaises(disputes.DisputeExists):
            disputes.open_case(self.invoice.pk, reason="QUALITY", description="again", actor=self.operator)

    def test_assign_and_notes_are_audited(self) -> None:
        case = disputes.open_case(self.invoice.pk, reason="OTHER", description="x", actor=self.operator)
        disputes.assign(case.pk, owner_id=self.approver.pk, actor=self.operator)
        disputes.add_note(case.pk, note="Called the client, waiting for documents.", actor=self.operator)
        case.refresh_from_db()
        self.assertEqual(case.owner, self.approver)
        self.assertEqual(case.notes.get().text, "Called the client, waiting for documents.")
        types = set(TimelineEvent.objects.filter(payload__dispute_id=str(case.pk)).values_list("type", flat=True))
        self.assertTrue({"DISPUTE_OPENED", "DISPUTE_ASSIGNED", "DISPUTE_NOTE_ADDED", "CHASE_HOLD_PLACED"} <= types)
        note_event = TimelineEvent.objects.get(type="DISPUTE_NOTE_ADDED")
        self.assertNotIn("documents", json.dumps(note_event.payload))  # notes stay out of the timeline payload


@override_settings(DEBUG=False, GMAIL_ALLOWED_RECIPIENTS=[])
class DisputeLockTests(TestCase):
    """An open dispute blocks every chasing path with invoice_disputed, including stale approvals."""

    def setUp(self) -> None:
        self.operator, self.approver = users()
        self.invoice = chased(days=20)

    def open(self) -> DisputeCase:
        return disputes.open_case(self.invoice.pk, reason="AMOUNT", description="Wrong amount", actor=self.operator)

    def test_draft_request_and_generation(self) -> None:
        self.open()
        with self.assertRaises(followups.InvoiceDisputed):
            followups.request_generation(self.invoice.pk, regenerate=False, actor=self.operator)
        with self.assertRaises(followups.InvoiceDisputed):
            followups.FollowUpGenerationService(gemini=StepGemini()).generate(self.invoice.pk, actor=self.operator)

    def test_approval_of_a_draft_made_before_the_dispute(self) -> None:
        draft = make_follow_up(self.invoice)
        self.open()
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.cancelled_reason), ("REJECTED", "dispute"))  # withdrawn
        with self.assertRaises(followups.FollowUpError):
            followups.approve(draft.pk, actor=self.approver)

    def test_stale_approval_cannot_be_sent(self) -> None:
        # An approval that somehow survived (e.g. made in the same instant) is still refused at reservation.
        approved = make_follow_up(self.invoice, FollowUp.Status.APPROVED)
        case = DisputeCase.objects.create(invoice=self.invoice, client=self.invoice.client, reason="AMOUNT",
                                          description="x", opened_by=self.operator)
        ChaseHold.objects.create(invoice=self.invoice, dispute_id=case.pk, reason="DISPUTE")
        with self.assertRaises(followups.InvoiceDisputed):
            FollowUpDeliveryService(FakeGmail()).request(approved.pk)
        approved.refresh_from_db()
        self.assertEqual(approved.status, "APPROVED")

    def test_queued_delivery_is_released_before_gmail(self) -> None:
        approved = make_follow_up(self.invoice, FollowUp.Status.APPROVED)
        gmail = FakeGmail()
        attempt = FollowUpDeliveryService(gmail).request(approved.pk, enqueue=False)  # reserved, queued
        self.open()
        with self.assertRaises(followups.InvoiceDisputed):
            FollowUpDeliveryService(gmail).deliver(attempt.pk)
        self.assertEqual(gmail.sent, [])
        attempt.refresh_from_db()
        self.assertEqual((attempt.status, attempt.error_code), ("FAILED", "invoice_disputed"))

    def test_scheduled_tick_and_automation_skip(self) -> None:
        self.open()
        enrollment = SequenceEnrollment.objects.get(invoice=self.invoice)
        SequenceEnrollment.objects.filter(pk=enrollment.pk).update(next_action_at=timezone.now())
        gemini = StepGemini()
        results = engine.run_tick(actor=ActorType.SCHEDULER, gemini=gemini)
        self.assertEqual(gemini.calls, [])
        self.assertEqual([r.reason for r in results], ["invoice_disputed"])

    def test_dispute_api_code_on_approve(self) -> None:
        self.client.force_login(self.approver)
        draft = make_follow_up(self.invoice)
        case = DisputeCase.objects.create(invoice=self.invoice, client=self.invoice.client, reason="AMOUNT",
                                          description="x", opened_by=self.operator)
        ChaseHold.objects.create(invoice=self.invoice, dispute_id=case.pk, reason="DISPUTE")
        response = self.client.post(f"/api/follow-ups/{draft.pk}/approve/")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "invoice_disputed"))


class ResolveDisputeTests(TestCase):
    def setUp(self) -> None:
        self.operator, self.approver = users()
        self.invoice = chased()
        self.case = disputes.open_case(self.invoice.pk, reason="AMOUNT", description="x", actor=self.operator)

    def test_only_an_approver_resolves_with_a_written_resolution(self) -> None:
        with self.assertRaises(ApproverRequired):
            disputes.resolve(self.case.pk, outcome="INVALID", resolution="ok", actor=self.operator)
        for kwargs in ({"outcome": "MAYBE", "resolution": "x"}, {"outcome": "INVALID", "resolution": ""}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InvalidCase):
                disputes.resolve(self.case.pk, actor=self.approver, **kwargs)

    def test_invalid_resumes_chasing_without_sending(self) -> None:
        with mock.patch("integrations.gmail.GmailClient.send", side_effect=AssertionError("must not send")):
            case, chasing = disputes.resolve(self.case.pk, outcome="INVALID", resolution="Contract says 600",
                                             actor=self.approver)
        self.assertEqual(case.status, "RESOLVED_INVALID")
        self.assertTrue(chasing["resumes"], chasing)
        self.assertEqual(active_holds(self.invoice), [])
        self.assertEqual(Task.objects.get(dispute=case).status, "DONE")
        self.assertEqual(SequenceEnrollment.objects.get(invoice=self.invoice).status, "ACTIVE")

    def test_valid_exits_and_is_not_re_enrolled_until_the_sheet_changes(self) -> None:
        disputes.resolve(self.case.pk, outcome="VALID", resolution="We overcharged", actor=self.approver)
        enrollment = SequenceEnrollment.objects.get(invoice=self.invoice)
        self.assertEqual((enrollment.status, enrollment.exit_reason), ("EXITED", "dispute_upheld"))
        engine.reconcile(actor=ActorType.SYSTEM)
        self.assertEqual(SequenceEnrollment.objects.filter(invoice=self.invoice).count(), 1)
        # The sheet corrects the amount: the invoice may be chased again.
        from apps.timeline import services as timeline
        from apps.timeline.models import EventType

        Invoice.objects.filter(pk=self.invoice.pk).update(amount="450.00")
        timeline.record(EventType.INVOICE_UPDATED, actor=ActorType.SYSTEM, invoice=self.invoice,
                        changes={"amount": {"from": "600.00", "to": "450.00"}})
        engine.reconcile(actor=ActorType.SYSTEM)
        self.assertEqual(SequenceEnrollment.objects.filter(invoice=self.invoice, status="ACTIVE").count(), 1)

    def test_resolving_twice_is_refused(self) -> None:
        disputes.resolve(self.case.pk, outcome="INVALID", resolution="x", actor=self.approver)
        with self.assertRaises(disputes.CaseError):
            disputes.resolve(self.case.pk, outcome="VALID", resolution="y", actor=self.approver)

    def test_resolving_does_not_clear_other_holds(self) -> None:
        reply(self.invoice, "Also please send the contract.", label="OTHER")
        case, chasing = disputes.resolve(self.case.pk, outcome="INVALID", resolution="x", actor=self.approver)
        self.assertEqual(active_holds(self.invoice), [("reply", "NEEDS_REVIEW")])
        self.assertFalse(chasing["resumes"])
        self.assertEqual([b["code"] for b in chasing["blocked_by"]], ["on_hold"])


class OverlappingHoldTests(TestCase):
    def test_promise_and_dispute_are_independent(self) -> None:
        operator, approver = users()
        invoice = chased()
        promise = promises.record(invoice.pk, promised_date=today_plus(5), actor=operator)
        with self.assertRaises(promises.PromiseExists):
            promises.record(invoice.pk, promised_date=today_plus(6), actor=operator)  # one open promise at a time
        case = disputes.open_case(invoice.pk, reason="QUALITY", description="x", actor=operator)
        promise.refresh_from_db()
        self.assertEqual(promise.status, "OPEN")  # a dispute does not end the promise
        with self.assertRaises(followups.InvoiceDisputed):
            promises.record(invoice.pk, promised_date=today_plus(6), actor=operator)
        self.assertEqual(active_holds(invoice), [("dispute", "DISPUTE"), ("promise", "PROMISE_TO_PAY")])
        disputes.resolve(case.pk, outcome="INVALID", resolution="x", actor=approver)
        self.assertEqual(active_holds(invoice), [("promise", "PROMISE_TO_PAY")])
        promises.cancel(promise.pk, note="withdrawn", actor=approver)
        self.assertEqual(active_holds(invoice), [])


class CaseApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def post(self, url: str, data: dict) -> object:  # type: ignore[type-arg]
        return self.client.post(url, data=json.dumps(data), content_type="application/json")

    def test_promise_and_dispute_endpoints(self) -> None:
        invoice = chased()
        response = self.post("/api/promises/", {"invoice_id": str(invoice.pk), "promised_date": today_plus(4).isoformat()})
        self.assertEqual(response.status_code, 201, response.content)  # type: ignore[attr-defined]
        promise = response.json()  # type: ignore[attr-defined]
        self.assertEqual((promise["status"], promise["grace_until"]), ("OPEN", today_plus(6).isoformat()))
        cancel = self.post(f"/api/promises/{promise['id']}/cancel/", {"note": "x"})
        self.assertEqual((cancel.status_code, cancel.json()["code"]), (403, "approver_required"))  # type: ignore[attr-defined]

        body = self.client.get(f"/api/invoices/{invoice.pk}/").json()
        self.assertEqual(body["open_promise"]["id"], promise["id"])
        self.assertEqual(body["holds"][0]["source"], "promise")
        self.assertIn("approver", body["holds"][0]["release_rule"])

        disputed = chased("INV-2", thread="thread-2")
        response = self.post("/api/disputes/", {"invoice_id": str(disputed.pk), "reason": "AMOUNT", "description": "x"})
        self.assertEqual(response.status_code, 201)  # type: ignore[attr-defined]
        case_id = response.json()["id"]  # type: ignore[attr-defined]
        self.assertEqual(self.post(f"/api/disputes/{case_id}/notes/", {"note": "hello"}).status_code, 200)  # type: ignore[attr-defined]
        resolve = self.post(f"/api/disputes/{case_id}/resolve/", {"outcome": "INVALID", "resolution": "x"})
        self.assertEqual(resolve.status_code, 403)  # type: ignore[attr-defined]

        approver = make_user("boss", Role.APPROVER)
        self.client.force_login(approver)
        resolve = self.post(f"/api/disputes/{case_id}/resolve/", {"outcome": "INVALID", "resolution": "Valid invoice"})
        self.assertEqual(resolve.status_code, 200, resolve.content)  # type: ignore[attr-defined]
        self.assertIn("chasing", resolve.json())  # type: ignore[attr-defined]
        self.assertEqual(len(self.client.get(f"/api/disputes/?invoice={disputed.pk}").json()), 1)
        self.assertEqual(self.client.get("/api/promises/?invoice=not-a-uuid").json(), [])

    def test_unknown_invoice_is_404(self) -> None:
        response = self.post("/api/promises/", {"invoice_id": "00000000-0000-0000-0000-000000000000",
                                                "promised_date": today_plus(3).isoformat()})
        self.assertEqual(response.status_code, 404)  # type: ignore[attr-defined]
