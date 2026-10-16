"""Runs created before workflows existed: close those that produced no drafts (nothing to review)."""

from django.db import migrations
from django.db.models import F


def finalize_legacy_runs(apps, schema_editor):  # type: ignore[no-untyped-def]
    AutomationRun = apps.get_model("invoices", "AutomationRun")
    AutomationRun.objects.filter(
        kind="AUTOMATION", finalized_at__isnull=True, drafts_generated=0, completed_at__isnull=False
    ).update(finalized_at=F("completed_at"))


class Migration(migrations.Migration):
    dependencies = [
        ("automation", "0002_workflow"),
        ("invoices", "0003_workflow"),
    ]

    operations = [migrations.RunPython(finalize_legacy_runs, migrations.RunPython.noop)]
