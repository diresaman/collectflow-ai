import datetime as dt
import json

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role
from apps.accounts.testing import SessionApiTestCase, make_user
from apps.cases import disputes, monitoring
from apps.inbox import services
from apps.inbox.models import Task
from apps.sequences.models import SequenceEnrollment
from apps.sequences.tests.helpers import StepGemini, sent, tick
from apps.sequences.tests.helpers import invoice as overdue
from apps.timeline.models import TimelineEvent

from .helpers import chased, reply, users


class TaskLifecycleTests(TestCase):
    def setUp(self) -> None:
        self.operator, self.approver = users()
        self.invoice = chased()

    def test_manual_task_assign_complete_with_note(self) -> None:
        task = services.create(self.invoice.pk, title="Ask about the PO number", actor=self.operator)
        self.assertEqual((task.kind, task.status, task.created_by), ("FOLLOW_UP", "OPEN", self.operator))
        services.assign(task.pk, assignee_id=self.approver.pk, actor=self.operator)
        services.mark_done(task.pk, actor=self.operator, note="PO is 4411")
        services.mark_done(task.pk, actor=self.approver, note="again")  # idempotent
        task.refresh_from_db()
        self.assertEqual((task.status, task.assignee, task.done_by, task.completion_note),
                         ("DONE", self.approver, self.operator, "PO is 4411"))
        self.assertEqual(TimelineEvent.objects.filter(type="TASK_DONE", payload__task_id=str(task.pk)).count(), 1)
        with self.assertRaises(services.TaskError):
            services.assign(task.pk, assignee_id=self.operator.pk, actor=self.operator)

    def test_cancel_needs_a_note_and_is_final(self) -> None:
        task = services.create(self.invoice.pk, title="Check", actor=self.operator)
        with self.assertRaises(services.InvalidTask):
            services.cancel(task.pk, actor=self.operator, note="")
        services.cancel(task.pk, actor=self.operator, note="Not needed")
        with self.assertRaises(services.TaskError):
            services.mark_done(task.pk, actor=self.operator)
        task.refresh_from_db()
        self.assertEqual((task.status, task.cancelled_by), ("CANCELLED", self.operator))

    def test_invalid_input_and_roles(self) -> None:
        with self.assertRaises(services.InvalidTask):
            services.create(self.invoice.pk, title="", actor=self.operator)
        task = services.create(self.invoice.pk, title="x", actor=self.operator)
        with self.assertRaises(services.InvalidTask):
            services.assign(task.pk, assignee_id=make_user("norole", role=None).pk, actor=self.operator)
        with self.assertRaises(services.TaskForbidden):
            services.mark_done(task.pk, actor=make_user("outsider", role=None))

    def test_call_step_task_advances_the_sequence_exactly_once_on_completion(self) -> None:
        inv = overdue("INV-9", days=40)
        sent(inv, "FRIENDLY", days_ago=30, thread="t9a")
        sent(inv, "PROFESSIONAL", days_ago=20, thread="t9b")
        sent(inv, "FIRM", days_ago=10, thread="t9c")
        tick(StepGemini())
        task = Task.objects.get(kind="CALL")
        with self.captureOnCommitCallbacks(execute=True):
            services.mark_done(task.pk, actor=self.operator, note="Spoke to AP")
        enrollment = SequenceEnrollment.objects.get(invoice=inv)
        self.assertEqual((enrollment.status, enrollment.last_completed_step), ("COMPLETED", 4))
        with self.captureOnCommitCallbacks(execute=True):
            services.mark_done(task.pk, actor=self.operator)
        self.assertEqual(TimelineEvent.objects.filter(type="STEP_COMPLETED", invoice=inv, payload__step=4).count(), 1)
        with self.assertRaises(services.TaskError):
            services.cancel(task.pk, actor=self.operator, note="no call")

    def test_completing_an_unrelated_task_does_not_resume_chasing(self) -> None:
        case = disputes.open_case(self.invoice.pk, reason="AMOUNT", description="x", actor=self.operator)
        message = reply(self.invoice, "Please send a copy too.", label="REQUESTS_INFO")
        info_task = Task.objects.get(reply=message, kind="SEND_INFO")
        with self.captureOnCommitCallbacks(execute=True):
            services.mark_done(info_task.pk, actor=self.operator)
            services.mark_done(Task.objects.get(dispute=case).pk, actor=self.operator)  # the review task itself
        from apps.followups.eligibility import ineligibility

        self.assertIn("invoice_disputed", [code for code, _ in ineligibility(self.invoice)])

    def test_overdue_tasks_are_flagged_once(self) -> None:
        task = services.create(self.invoice.pk, title="x", due_at=(timezone.now() - dt.timedelta(hours=1)).isoformat(),
                               actor=self.operator)
        self.assertTrue(task.is_overdue)
        self.assertEqual(monitoring.flag_overdue_tasks(), 1)
        self.assertEqual(monitoring.flag_overdue_tasks(), 0)
        self.assertEqual(TimelineEvent.objects.filter(type="TASK_OVERDUE").count(), 1)


class TaskApiTests(SessionApiTestCase):
    role = Role.OPERATOR

    def post(self, url: str, data: dict) -> object:  # type: ignore[type-arg]
        return self.client.post(url, data=json.dumps(data), content_type="application/json")

    def test_task_endpoints(self) -> None:
        invoice = chased()
        created = self.post("/api/tasks/", {"invoice_id": str(invoice.pk), "title": "Call AP", "assignee_id": self.user.pk})
        self.assertEqual(created.status_code, 201, created.content)  # type: ignore[attr-defined]
        task = created.json()  # type: ignore[attr-defined]
        self.assertEqual((task["assignee"]["username"], task["kind_display"]), (self.user.username, "Other follow-up"))
        mine = self.client.get("/api/tasks/?assignee=me&open=true").json()
        self.assertEqual([t["id"] for t in mine], [task["id"]])
        done = self.post(f"/api/tasks/{task['id']}/done/", {"note": "Called"})
        self.assertEqual((done.status_code, done.json()["status"]), (200, "DONE"))  # type: ignore[attr-defined]
        self.assertEqual(self.client.get("/api/tasks/?status=DONE").json()[0]["completion_note"], "Called")
        users = self.client.get("/api/users/").json()
        self.assertIn(self.user.username, [u["username"] for u in users])
        self.assertNotIn("email", users[0])
