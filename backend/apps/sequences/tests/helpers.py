import datetime as dt
from typing import Any

from django.utils import timezone

from apps.followups.models import DeliveryAttempt, FollowUp
from apps.followups.tests.helpers import make_follow_up, overdue_invoice
from apps.invoices.models import Invoice
from apps.sequences import engine
from apps.timeline.models import ActorType


class StepGemini:
    """Fake Gemini that writes a valid draft in whatever tone the backend pins in the schema."""

    model = "fake-gemini"

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        tone = kwargs["response_schema"]["properties"]["tone"]["enum"][0]
        return {
            "subject": f"{tone.title()} reminder: invoice",
            "body": "Hello, this is a reminder about your invoice. Accounts Receivable Team",
            "tone": tone,
            "reasoning": "Sequence step.",
            "recommended_action": "Review and approve.",
        }

    def tones(self) -> list[str]:
        return [call["response_schema"]["properties"]["tone"]["enum"][0] for call in self.calls]


def invoice(external_id: str = "INV-1", days: int = 5, **fields: Any) -> Invoice:
    return overdue_invoice(days=days, external_id=external_id, client_email=f"ap-{external_id.lower()}@fictional.test",
                           **fields)


def sent(inv: Invoice, tone: str, *, days_ago: int, thread: str = "thread-1", message_id: str = "",
         **fields: Any) -> FollowUp:
    """A reminder that Gmail accepted `days_ago` days ago, with its delivery attempt."""
    when = timezone.now() - dt.timedelta(days=days_ago)
    follow_up = make_follow_up(inv, FollowUp.Status.SENT, tone=tone, sent_at=when, generated_at=when,
                               subject=f"Reminder: invoice {inv.external_id}", **fields)
    DeliveryAttempt.objects.create(
        follow_up=follow_up, status=DeliveryAttempt.Status.SENT, recipient_email=inv.client_email,
        started_at=when, completed_at=when, gmail_message_id=f"gm-{follow_up.pk}", gmail_thread_id=thread,
        rfc_message_id=message_id or f"<{follow_up.pk}@example.test>",
    )
    return follow_up


def tick(gemini: StepGemini | None = None, **kwargs: Any) -> list[engine.StepResult]:
    return engine.run_tick(actor=ActorType.SYSTEM, gemini=gemini or StepGemini(), **kwargs)


def enrollment_of(inv: Invoice) -> Any:
    return inv.enrollments.order_by("-created_at").first()


def set_status(follow_up: FollowUp, status: str, **fields: Any) -> None:
    FollowUp.objects.filter(pk=follow_up.pk).update(status=status, **fields)
