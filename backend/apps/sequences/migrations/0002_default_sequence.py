from django.db import migrations

DEFAULT_STEPS = [
    # order, offset days after the due date, channel, tone, requires an approver
    (1, 1, "EMAIL", "FRIENDLY", False),
    (2, 8, "EMAIL", "PROFESSIONAL", False),
    (3, 15, "EMAIL", "FIRM", True),
    (4, 30, "TASK", "", False),
]


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    """The v2 default cadence (§6). Idempotent; never touches an existing sequence."""
    Sequence = apps.get_model("sequences", "Sequence")
    SequenceStep = apps.get_model("sequences", "SequenceStep")
    sequence, created = Sequence.objects.get_or_create(
        name="Standard collection", version=1, defaults={"is_default": True, "active": True}
    )
    if created:
        for order, offset, channel, tone, approver in DEFAULT_STEPS:
            SequenceStep.objects.create(
                sequence=sequence, order=order, offset_days=offset, channel=channel, tone=tone,
                requires_approver=approver,
            )


def unseed(apps, schema_editor):  # type: ignore[no-untyped-def]
    """Removes the seeded sequence only if no enrollment ever used it."""
    Sequence = apps.get_model("sequences", "Sequence")
    SequenceEnrollment = apps.get_model("sequences", "SequenceEnrollment")
    SequenceStep = apps.get_model("sequences", "SequenceStep")
    used = SequenceEnrollment.objects.values_list("sequence_id", flat=True)
    ids = list(Sequence.objects.filter(name="Standard collection", version=1).exclude(pk__in=used).values_list("pk", flat=True))
    # Direct deletes: nothing references these rows (unused), and the delete collector cannot follow
    # relations of historical models here.
    SequenceStep.objects.filter(sequence_id__in=ids)._raw_delete(schema_editor.connection.alias)
    Sequence.objects.filter(pk__in=ids)._raw_delete(schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [
        ("sequences", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
