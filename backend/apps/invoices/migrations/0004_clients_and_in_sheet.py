import uuid

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


def backfill(apps, schema_editor):  # type: ignore[no-untyped-def]
    """One Client per lowercased invoice email; in_sheet from the latest successful sync."""
    Client = apps.get_model("invoices", "Client")
    Invoice = apps.get_model("invoices", "Invoice")
    AutomationRun = apps.get_model("invoices", "AutomationRun")

    clients: dict[str, object] = {}
    for invoice in Invoice.objects.order_by("-updated_at"):
        email = invoice.client_email.strip().lower()
        if email not in clients:
            clients[email] = Client.objects.create(email=email, display_name=invoice.client_name)
        invoice.client = clients[email]
        invoice.save(update_fields=["client"])

    latest_sync = (
        AutomationRun.objects.filter(kind="SYNC", status="SUCCEEDED")
        .order_by("-started_at", "-id")
        .values_list("started_at", flat=True)
        .first()
    )
    if latest_sync is not None:
        Invoice.objects.filter(last_synced_at__lt=latest_sync).update(in_sheet=False)


class Migration(migrations.Migration):
    dependencies = [
        ("invoices", "0003_workflow"),
    ]

    operations = [
        migrations.CreateModel(
            name="Client",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("display_name", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["display_name", "email"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("email", django.db.models.functions.text.Lower("email"))),
                        name="client_email_lowercase",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="invoice",
            name="client",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoices",
                to="invoices.client",
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="in_sheet",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invoice",
            name="client",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoices",
                to="invoices.client",
            ),
        ),
    ]
