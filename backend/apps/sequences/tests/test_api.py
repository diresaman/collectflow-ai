import datetime as dt
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.followups.models import FollowUp
from apps.followups.tests.helpers import make_follow_up
from apps.inbox.models import Task
from apps.invoices.models import AutomationRun
from apps.sequences import engine
from apps.sequences.models import Sequence, SequenceEnrollment
from apps.timeline.models import TimelineEvent

from .helpers import StepGemini, enrollment_of, invoice, sent, tick

JSON = "application/json"


class FourEyesApprovalTests(SessionApiTestCase):
    """v2 §6: firm tone, an approver-required step or a high amount need an APPROVER other than the last editor."""

    def draft(self, tone: str = "FRIENDLY", amount: str = "1200.00", external_id: str = "INV-1") -> FollowUp:
        inv = invoice(external_id, amount=Decimal(amount))
        return make_follow_up(inv, FollowUp.Status.DRAFT, tone=tone)

    def approve(self, follow_up: FollowUp):  # type: ignore[no-untyped-def]
        return self.client.post(reverse("follow-up-approve", args=[follow_up.pk]))

    def test_operator_may_approve_an_ordinary_reminder(self) -> None:
        response = self.approve(self.draft())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approval"]["required_role"], "OPERATOR")

    def test_firm_tone_needs_an_approver_and_explains_why(self) -> None:
        draft = self.draft(tone="FIRM")

        detail = self.client.get(reverse("follow-up-detail", args=[draft.pk])).json()
        self.assertEqual(detail["approval"]["required_role"], "APPROVER")
        self.assertEqual(detail["approval"]["blocked_for_you"]["code"], "approver_required")
        self.assertIn("Firm tone", detail["approval"]["reasons"])

        response = self.approve(draft)
        self.assertEqual((response.status_code, response.json()["code"]), (403, "approver_required"))
        draft.refresh_from_db()
        self.assertEqual(draft.status, "DRAFT")

    def test_high_value_needs_an_approver(self) -> None:
        response = self.approve(self.draft(amount="5000.00", external_id="HIGH"))
        self.assertEqual((response.status_code, response.json()["code"]), (403, "approver_required"))
        self.assertEqual(self.approve(self.draft(amount="4999.99", external_id="BELOW")).status_code, 200)

    def test_approver_who_edited_cannot_approve_but_another_can(self) -> None:
        # v2 §12: "Operator edits a firm draft, then approves it → 403 self_approval; a different approver can approve".
        editor = make_user("editor", Role.APPROVER)
        self.client.force_login(editor)
        draft = self.draft(tone="FIRM")
        self.client.patch(reverse("follow-up-detail", args=[draft.pk]), {"body": "An edited firm reminder body text."},
                          content_type=JSON)

        response = self.approve(draft)
        self.assertEqual((response.status_code, response.json()["code"]), (403, "self_approval"))

        self.client.force_login(make_user("second", Role.APPROVER))
        response = self.approve(draft)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approved_by"], "second")

    def test_step_requiring_an_approver(self) -> None:
        inv = invoice(days=30)
        sent(inv, "FRIENDLY", days_ago=20)
        sent(inv, "PROFESSIONAL", days_ago=10)
        tick(StepGemini())
        draft = FollowUp.objects.get(status="DRAFT")
        self.assertEqual((draft.step.order, draft.step.requires_approver), (3, True))
        self.assertEqual(self.approve(draft).json()["code"], "approver_required")


class SequenceApiTests(SessionApiTestCase):
    def test_list_shows_steps_and_enrollment_counts(self) -> None:
        invoice()
        tick(StepGemini())
        body = self.client.get(reverse("sequence-list")).json()
        default = next(s for s in body if s["is_default"])
        self.assertEqual([(s["order"], s["channel"], s["tone"]) for s in default["steps"]],
                         [(1, "EMAIL", "FRIENDLY"), (2, "EMAIL", "PROFESSIONAL"), (3, "EMAIL", "FIRM"), (4, "TASK", "")])
        self.assertEqual(default["enrollment_counts"]["by_step"], {"1": 1})

    def test_only_admins_create_versions(self) -> None:
        steps = [{"offset_days": 2, "channel": "EMAIL", "tone": "FRIENDLY"},
                 {"offset_days": 10, "channel": "TASK"}]
        response = self.client.post(reverse("sequence-list"), {"name": "Short", "steps": steps}, content_type=JSON)
        self.assertEqual(response.status_code, 403)

        self.client.force_login(make_user("admin", Role.ADMIN))
        response = self.client.post(reverse("sequence-list"), {"name": "Short", "steps": steps, "make_default": True},
                                    content_type=JSON)
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["version"], response.json()["is_default"]), (1, True))
        again = self.client.post(reverse("sequence-list"), {"name": "Short", "steps": steps}, content_type=JSON)
        self.assertEqual(again.json()["version"], 2)  # a new version; existing versions never change
        self.assertEqual(Sequence.objects.filter(is_default=True).count(), 1)

    def test_invalid_sequences_are_rejected(self) -> None:
        self.client.force_login(make_user("admin", Role.ADMIN))
        cases = [
            [{"offset_days": 1, "channel": "EMAIL", "tone": "FRIENDLY"}],  # one step
            [{"offset_days": 5, "channel": "EMAIL", "tone": "FRIENDLY"}, {"offset_days": 5, "channel": "TASK"}],
            [{"offset_days": 1, "channel": "EMAIL"}, {"offset_days": 5, "channel": "TASK"}],  # no tone
            [{"offset_days": 1, "channel": "EMAIL", "tone": "ANGRY"}, {"offset_days": 5, "channel": "TASK"}],
        ]
        for steps in cases:
            with self.subTest(steps=steps):
                response = self.client.post(reverse("sequence-list"), {"name": "X", "steps": steps}, content_type=JSON)
                self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_sequence"))


class EnrollmentApiTests(SessionApiTestCase):
    def test_pause_and_resume(self) -> None:
        inv = invoice()
        engine.reconcile(actor=None)
        enrollment = enrollment_of(inv)
        url = lambda name: reverse(name, args=[enrollment.pk])  # noqa: E731

        self.assertEqual(self.client.post(url("enrollment-pause"), {}, content_type=JSON).status_code, 400)
        paused = self.client.post(url("enrollment-pause"), {"reason": "Client called"}, content_type=JSON)
        self.assertEqual((paused.json()["status"], paused.json()["pause_reason"]), ("PAUSED", "Client called"))
        self.assertEqual(self.client.post(url("enrollment-pause"), {"reason": "x"}, content_type=JSON).status_code, 409)
        resumed = self.client.post(url("enrollment-resume"), content_type=JSON)
        self.assertEqual(resumed.json()["status"], "ACTIVE")
        self.assertEqual(
            list(TimelineEvent.objects.filter(type__startswith="ENROLLMENT_").values_list("type", flat=True).order_by("occurred_at")),
            ["ENROLLMENT_PAUSED", "ENROLLMENT_RESUMED"],
        )

    def test_history_review_needs_an_approver(self) -> None:
        inv = invoice(days=30)
        sent(inv, "PROFESSIONAL", days_ago=20)
        sent(inv, "PROFESSIONAL", days_ago=10)
        engine.reconcile(actor=None)
        enrollment = enrollment_of(inv)
        url = reverse("enrollment-review", args=[enrollment.pk])

        self.assertEqual(self.client.post(reverse("enrollment-resume", args=[enrollment.pk])).status_code, 409)
        self.assertEqual(self.client.post(url, {"last_completed_step": 1}, content_type=JSON).status_code, 403)

        self.client.force_login(make_user("approver", Role.APPROVER))
        self.assertEqual(self.client.post(url, {"last_completed_step": 9}, content_type=JSON).status_code, 400)
        response = self.client.post(url, {"last_completed_step": 1, "note": "Treat the two as step 1"}, content_type=JSON)
        body = response.json()
        self.assertEqual((body["status"], body["review_required"], body["last_completed_step"]), ("ACTIVE", False, 1))
        self.assertEqual([s["state"] for s in body["steps"]], ["done", "upcoming", "upcoming", "upcoming"])
        self.assertTrue(TimelineEvent.objects.filter(type="ENROLLMENT_REVIEWED", actor__username="approver").exists())

    def test_invoice_list_includes_the_enrollment(self) -> None:
        inv = invoice()
        tick(StepGemini())
        row = next(i for i in self.client.get(reverse("invoice-list")).json() if i["id"] == str(inv.pk))
        self.assertEqual((row["enrollment"]["pending_step"], row["enrollment"]["total_steps"]), (1, 4))
        listing = self.client.get(reverse("enrollment-list"), {"invoice": str(inv.pk)}).json()
        self.assertEqual(listing[0]["invoice_external_id"], inv.external_id)


class TaskApiTests(SessionApiTestCase):
    def test_list_and_complete_tasks(self) -> None:
        inv = invoice(days=40)
        for tone, days in (("FRIENDLY", 30), ("PROFESSIONAL", 20), ("FIRM", 10)):
            sent(inv, tone, days_ago=days)
        tick(StepGemini())
        task = Task.objects.get()

        listing = self.client.get(reverse("task-list"), {"open": "true"}).json()
        self.assertEqual((listing[0]["kind"], listing[0]["step"]), ("CALL", 4))
        done = self.client.post(reverse("task-done", args=[task.pk]))
        self.assertEqual(done.json()["done_by"], self.user.username)
        self.client.post(reverse("task-done", args=[task.pk]))  # idempotent
        self.assertEqual(TimelineEvent.objects.filter(type="TASK_DONE").count(), 1)
        self.assertEqual(self.client.get(reverse("task-list"), {"open": "true"}).json(), [])


class DashboardStalenessTests(SessionApiTestCase):
    def test_stale_after_26_hours_without_a_successful_sync(self) -> None:
        self.assertTrue(self.client.get(reverse("dashboard")).json()["stale"])
        run = AutomationRun.objects.create(trigger="MANUAL", status="SUCCEEDED", completed_at=timezone.now())
        body = self.client.get(reverse("dashboard")).json()
        self.assertFalse(body["stale"])
        AutomationRun.objects.filter(pk=run.pk).update(completed_at=timezone.now() - dt.timedelta(hours=27))
        self.assertTrue(self.client.get(reverse("dashboard")).json()["stale"])
        self.assertEqual(SequenceEnrollment.objects.count(), 0)
