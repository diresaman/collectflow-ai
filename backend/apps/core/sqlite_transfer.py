"""
One-time transfer of a CollectFlow SQLite database into PostgreSQL (M08.1).

`transfer()` runs as one PostgreSQL transaction:
  1. preflight: the source is a consistent, fully migrated snapshot with no live worker or run;
     the target is PostgreSQL, fully migrated and empty;
  2. copy every application row with its original primary key (in-memory serialization, so
     timestamps keep their microseconds);
  3. verify: row-by-row equality of every copied model, foreign keys, sequences, status totals
     and a timeline digest. Any difference aborts and rolls back;
  4. apply the cutover policy to unfinished work (section CUTOVER below);
  5. commit, or roll back for a dry run.

The source is only read. No provider (Sheets, Gemini, Gmail) is called.

CUTOVER policy for work that was unfinished in the snapshot:
  - PENDING delivery job: nothing reached Gmail, so the reservation is released (attempt FAILED
    "cutover_not_sent", reminder back to APPROVED) and the job is cancelled. A person sends it again.
  - RUNNING delivery job: Gmail may have been called, so the job fails as "abandoned" and the
    reminder stays SENDING for `reconcile_delivery`. It is never retried.
  - Every other PENDING or RUNNING job is cancelled ("cutover_cancelled"); a sync run it was
    executing is marked FAILED. Nothing is re-run automatically.
  - RUNNING automation runs fail as abandoned; the automation lease is released.
  - SENDING / DELIVERY_UNKNOWN reminders and their attempts are copied unchanged (reconcile them).
"""

import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.core import serializers
from django.core.management.color import no_style
from django.db import connections, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import Model
from django.utils import timezone

# Apps whose rows are copied. ContentTypes and Permissions are recreated by `migrate` on the target
# and referenced by natural key; sessions and admin log entries are not copied (users sign in again).
TRANSFER_APPS = ("auth", "accounts", "invoices", "followups", "automation", "timeline")
EXCLUDED_MODELS = ("auth.permission",)
NOT_TRANSFERRED = ("contenttypes.contenttype", "auth.permission", "sessions.session", "admin.logentry")

LIVE_WORKER_WINDOW = dt.timedelta(minutes=2)
CUTOVER_SUMMARY = "Cancelled at the PostgreSQL cutover; nothing was re-run."


SOURCE_ALIAS_HELP = (
    "The source is the `sqlite_source` database alias, defined when SQLITE_SOURCE_PATH is set. Point it at a "
    "backup taken with the application stopped, never at the live file."
)


class TransferError(Exception):
    """A preflight or verification failure. Nothing was committed to the target."""


class _Rollback(Exception):
    pass


@dataclass
class TransferReport:
    source: str
    target: str
    dry_run: bool
    started_at: dt.datetime = field(default_factory=timezone.now)
    counts: dict[str, tuple[int, int]] = field(default_factory=dict)  # label -> (source, target)
    not_transferred: dict[str, int] = field(default_factory=dict)
    totals: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {"source": {...}, "target": {...}}
    timeline_digest: tuple[str, str] = ("", "")
    sequences: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)  # table -> (max id, last value)
    json_checks: list[str] = field(default_factory=list)
    cutover_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    committed: bool = False

    def to_markdown(self) -> str:
        lines = [
            "# SQLite → PostgreSQL transfer report",
            "",
            f"- Source: `{self.source}`",
            f"- Target: `{self.target}`",
            f"- Started: {self.started_at.isoformat()}",
            f"- Mode: {'DRY RUN (rolled back)' if self.dry_run else 'transfer'}",
            f"- Result: {'committed' if self.committed else 'not committed'}",
            "",
            "## Row counts (every row compared field by field, including primary keys)",
            "",
            "| Model | Source | Target | Match |",
            "| --- | ---: | ---: | --- |",
        ]
        for label, (source, target) in sorted(self.counts.items()):
            lines.append(f"| `{label}` | {source} | {target} | {'yes' if source == target else '**NO**'} |")
        lines += ["", "## Not transferred (recreated or ephemeral)", ""]
        lines += [f"- `{label}`: {count} row(s) in the source" for label, count in sorted(self.not_transferred.items())]
        lines += ["", "## Status totals", ""]
        for name, sides in self.totals.items():
            lines.append(f"- **{name}**: source {sides['source']} · target {sides['target']}")
        lines += [
            "",
            "## Timeline",
            "",
            f"- SHA-256 over (id, type, actor, invoice, client, occurred_at, payload), ordered by id: "
            f"source `{self.timeline_digest[0][:16]}…`, target `{self.timeline_digest[1][:16]}…` "
            f"({'match' if self.timeline_digest[0] == self.timeline_digest[1] else 'MISMATCH'})",
            "",
            "## Primary-key sequences (next id must exceed the copied maximum)",
            "",
        ]
        lines += [f"- `{table}`: max id {mx}, sequence at {last}" for table, (mx, last) in sorted(self.sequences.items())]
        lines += ["", "## JSON and relationship checks", ""] + [f"- {item}" for item in self.json_checks]
        lines += ["", "## Cutover actions on unfinished work", ""]
        lines += [f"- {item}" for item in self.cutover_actions] or ["- None: no unfinished work in the snapshot."]
        if self.warnings:
            lines += ["", "## Warnings", ""] + [f"- {item}" for item in self.warnings]
        return "\n".join(lines) + "\n"


def transferred_models() -> list[type[Model]]:
    return [
        model
        for model in apps.get_models()
        if model._meta.app_label in TRANSFER_APPS and model._meta.label_lower not in EXCLUDED_MODELS
    ]


def transfer(*, source_alias: str, target_alias: str = "default", dry_run: bool = False) -> TransferReport:
    source, target = connections[source_alias], connections[target_alias]
    report = TransferReport(source=str(source.settings_dict["NAME"]), target=_describe(target), dry_run=dry_run)

    _preflight(source_alias, target_alias, report)

    try:
        with transaction.atomic(using=target_alias):
            _copy(source_alias, target_alias)
            with target.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # surface FK problems now, not at commit
            _verify(source_alias, target_alias, report)
            _apply_cutover_policy(target_alias, report)
            if dry_run:
                raise _Rollback
    except _Rollback:
        report.committed = False
        return report
    report.committed = True
    return report


# Preflight -----------------------------------------------------------------------


def _describe(conn: Any) -> str:
    s = conn.settings_dict
    return f"{conn.vendor}://{s.get('USER')}@{s.get('HOST')}:{s.get('PORT')}/{s.get('NAME')}"


def _preflight(source_alias: str, target_alias: str, report: TransferReport) -> None:
    source, target = connections[source_alias], connections[target_alias]
    if source.vendor != "sqlite":
        raise TransferError(f"The source must be a SQLite database (got {source.vendor}).")
    if target.vendor != "postgresql":
        raise TransferError(f"The target must be PostgreSQL (got {target.vendor}).")

    with source.cursor() as cursor:
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
    if result != "ok":
        raise TransferError(f"The SQLite source failed PRAGMA integrity_check: {result}")

    source_migrations = set(MigrationRecorder(source).applied_migrations())
    target_migrations = set(MigrationRecorder(target).applied_migrations())
    missing_in_source = sorted(f"{a}.{n}" for a, n in target_migrations - source_migrations)
    unknown_in_source = sorted(f"{a}.{n}" for a, n in source_migrations - target_migrations)
    if missing_in_source or unknown_in_source:
        raise TransferError(
            "Source and target schemas differ. Run `migrate` on the target, and migrate a COPY of the SQLite "
            f"file to the same migrations. Missing in source: {missing_in_source or 'none'}; "
            f"unknown to target: {unknown_in_source or 'none'}."
        )

    # The snapshot must come from stopped processes.
    Heartbeat = apps.get_model("automation", "WorkerHeartbeat")
    Lease = apps.get_model("automation", "AutomationLease")
    now = timezone.now()
    for beat in Heartbeat.objects.using(source_alias).all():
        if now - beat.last_seen_at < LIVE_WORKER_WINDOW:
            raise TransferError(
                f"A worker was alive {int((now - beat.last_seen_at).total_seconds())} s before this snapshot. "
                "Stop run_worker, runserver and cron, then take a new backup."
            )
    for lease in Lease.objects.using(source_alias).exclude(owner=""):
        if lease.expires_at and lease.expires_at > now:
            raise TransferError("The automation lease is held in the snapshot: an automation run is in progress.")

    non_empty = [
        model._meta.label for model in transferred_models() if model.objects.using(target_alias).exists()
    ]
    if non_empty:
        raise TransferError(
            "The PostgreSQL target already contains data in: " + ", ".join(sorted(non_empty))
            + ". Transfer only into a freshly migrated, empty database."
        )

    for label in NOT_TRANSFERRED:
        model = apps.get_model(label)
        report.not_transferred[label] = model.objects.using(source_alias).count()
    if report.not_transferred.get("sessions.session"):
        report.warnings.append("Sessions are not copied: signed-in users must sign in again.")


# Copy ----------------------------------------------------------------------------


def _serialize(model: type[Model], alias: str, fmt: str = "python") -> Any:
    queryset = model._base_manager.using(alias).order_by("pk")
    return serializers.serialize(fmt, queryset, use_natural_foreign_keys=True)


def _copy(source_alias: str, target_alias: str) -> None:
    """
    Inserts every source row into the target with its original primary key.

    Uses Django's in-memory "python" serializer, not a JSON fixture: the JSON encoder truncates
    timestamps to milliseconds, which would silently change every stored datetime. Saves are raw
    (no model save() logic, no signals). Foreign keys are DEFERRABLE on PostgreSQL, so the order of
    models does not matter inside the transaction.
    """
    copied: list[type[Model]] = []
    for model in transferred_models():
        rows = _serialize(model, source_alias, "python")
        for deserialized in serializers.deserialize("python", rows, using=target_alias, handle_forward_references=True):
            deserialized.save(using=target_alias)
        copied.append(model)

    # Identity sequences must continue after the copied ids.
    connection = connections[target_alias]
    with connection.cursor() as cursor:
        for statement in connection.ops.sequence_reset_sql(no_style(), copied):
            cursor.execute(statement)


# Verification --------------------------------------------------------------------


def _verify(source_alias: str, target_alias: str, report: TransferReport) -> None:
    problems: list[str] = []
    for model in transferred_models():
        label = model._meta.label_lower
        source_rows = _serialize(model, source_alias, "python")
        target_rows = _serialize(model, target_alias, "python")
        report.counts[label] = (len(source_rows), len(target_rows))
        if source_rows != target_rows:
            differing = [
                str(s.get("pk")) for s, t in zip(source_rows, target_rows) if s != t
            ][:5]
            problems.append(f"{label}: rows differ (count {len(source_rows)} vs {len(target_rows)}; e.g. {differing})")

    report.totals = {name: {"source": fn(source_alias), "target": fn(target_alias)} for name, fn in STATUS_TOTALS.items()}
    for name, sides in report.totals.items():
        if sides["source"] != sides["target"]:
            problems.append(f"status totals differ for {name}")

    report.timeline_digest = (_timeline_digest(source_alias), _timeline_digest(target_alias))
    if report.timeline_digest[0] != report.timeline_digest[1]:
        problems.append("timeline digest differs")

    _check_sequences(target_alias, report, problems)
    _json_and_relationship_checks(target_alias, report, problems)

    if problems:
        raise TransferError("Verification failed; nothing was committed:\n- " + "\n- ".join(problems))


def _totals(label: str, field_name: str) -> Any:
    def compute(alias: str) -> dict[str, int]:
        model = apps.get_model(label)
        return dict(sorted(Counter(model.objects.using(alias).values_list(field_name, flat=True)).items()))

    return compute


STATUS_TOTALS = {
    "invoices by status": _totals("invoices.Invoice", "status"),
    "invoices in sheet": _totals("invoices.Invoice", "in_sheet"),
    "follow-ups by status": _totals("followups.FollowUp", "status"),
    "delivery attempts by status": _totals("followups.DeliveryAttempt", "status"),
    "automation runs by status": _totals("invoices.AutomationRun", "status"),
    "outbox jobs by status": _totals("automation.OutboxJob", "status"),
    "users by role": _totals("accounts.Profile", "role"),
}


def _timeline_digest(alias: str) -> str:
    Event = apps.get_model("timeline", "TimelineEvent")
    digest = hashlib.sha256()
    rows = Event.objects.using(alias).order_by("id").values_list(
        "id", "type", "actor_type", "actor_id", "invoice_id", "client_id", "occurred_at", "payload"
    )
    for row in rows:
        *head, occurred_at, payload = row
        digest.update(json.dumps([str(v) for v in head] + [occurred_at.isoformat(), payload], sort_keys=True).encode())
    return digest.hexdigest()


def _check_sequences(alias: str, report: TransferReport, problems: list[str]) -> None:
    conn = connections[alias]
    with conn.cursor() as cursor:
        for model in transferred_models():
            pk = model._meta.pk
            if pk.get_internal_type() not in ("AutoField", "BigAutoField"):
                continue
            table = model._meta.db_table
            cursor.execute("SELECT pg_get_serial_sequence(%s, %s)", [table, pk.column])
            sequence = cursor.fetchone()[0]
            cursor.execute(f'SELECT MAX("{pk.column}") FROM "{table}"')
            max_id = cursor.fetchone()[0]
            if sequence is None:
                continue
            cursor.execute(f"SELECT last_value, is_called FROM {sequence}")
            last_value, is_called = cursor.fetchone()
            next_id = last_value + 1 if is_called else last_value
            report.sequences[table] = (max_id, last_value)
            if max_id is not None and next_id <= max_id:
                problems.append(f"sequence {sequence} would reuse id {next_id} (max {max_id})")


def _json_and_relationship_checks(alias: str, report: TransferReport, problems: list[str]) -> None:
    FollowUp = apps.get_model("followups", "FollowUp")
    Invoice = apps.get_model("invoices", "Invoice")
    AutomationRun = apps.get_model("invoices", "AutomationRun")
    Event = apps.get_model("timeline", "TimelineEvent")

    mismatched = [
        str(f.pk) for f in FollowUp.objects.using(alias).select_related("invoice")
        if f.invoice_snapshot.get("external_id") != f.invoice.external_id
    ]
    report.json_checks.append(
        f"Follow-up snapshots naming their own invoice: {FollowUp.objects.using(alias).count() - len(mismatched)}"
        f"/{FollowUp.objects.using(alias).count()}"
    )
    if mismatched:
        problems.append(f"follow-up snapshots that name another invoice: {mismatched[:5]}")

    wrong_client = Invoice.objects.using(alias).exclude(client__email=_lower_email()).count()
    report.json_checks.append(f"Invoices whose client email matches their own (lowercased): "
                              f"{Invoice.objects.using(alias).count() - wrong_client}/{Invoice.objects.using(alias).count()}")
    if wrong_client:
        problems.append(f"{wrong_client} invoice(s) attached to a client with a different email")

    errors = sum(len(run.errors) for run in AutomationRun.objects.using(alias).exclude(errors=[]))
    report.json_checks.append(f"Sync row errors (JSON) preserved: {errors} entries")
    payloads = Event.objects.using(alias).exclude(payload={}).count()
    report.json_checks.append(f"Timeline events with a JSON payload: {payloads}")


def _lower_email() -> Any:
    from django.db.models.functions import Lower

    return Lower("client_email")


# Cutover policy ------------------------------------------------------------------


def _apply_cutover_policy(alias: str, report: TransferReport) -> None:
    from apps.timeline.models import ActorType, EventType

    OutboxJob = apps.get_model("automation", "OutboxJob")
    DeliveryAttempt = apps.get_model("followups", "DeliveryAttempt")
    FollowUp = apps.get_model("followups", "FollowUp")
    AutomationRun = apps.get_model("invoices", "AutomationRun")
    WorkflowStep = apps.get_model("automation", "WorkflowStep")
    Lease = apps.get_model("automation", "AutomationLease")
    Event = apps.get_model("timeline", "TimelineEvent")
    now = timezone.now()

    for job in OutboxJob.objects.using(alias).filter(status__in=["PENDING", "RUNNING"]).order_by("created_at"):
        if job.kind == "followups.deliver":
            attempt = DeliveryAttempt.objects.using(alias).filter(pk=job.payload.get("attempt_id")).first()
            if job.status == "PENDING" and attempt is not None and attempt.status == "SENDING":
                DeliveryAttempt.objects.using(alias).filter(pk=attempt.pk, status="SENDING").update(
                    status="FAILED", completed_at=now, error_code="cutover_not_sent",
                    error_summary="Not sent: the queued delivery was cancelled at the PostgreSQL cutover. "
                                  "Confirm the send again.",
                )
                follow_up = FollowUp.objects.using(alias).select_related("invoice").get(pk=attempt.follow_up_id)
                FollowUp.objects.using(alias).filter(pk=follow_up.pk, status="SENDING").update(
                    status="APPROVED", updated_at=now
                )
                Event.objects.using(alias).create(
                    type=EventType.SEND_CANCELLED, actor_type=ActorType.SYSTEM, invoice_id=follow_up.invoice_id,
                    client_id=follow_up.invoice.client_id,
                    payload={"follow_up_id": str(follow_up.pk), "attempt_id": str(attempt.pk),
                             "error_code": "cutover_not_sent"},
                )
                _close_job(OutboxJob, alias, job, "cutover_cancelled", now)
                report.cutover_actions.append(
                    f"Queued delivery job {job.pk}: never submitted; attempt {attempt.pk} FAILED (cutover_not_sent), "
                    f"reminder {follow_up.pk} back to APPROVED."
                )
            else:
                _close_job(OutboxJob, alias, job, "abandoned", now)
                report.cutover_actions.append(
                    f"Delivery job {job.pk} was {job.status}: Gmail may have been called. Job FAILED (abandoned); "
                    f"attempt {getattr(attempt, 'pk', '?')} left as {getattr(attempt, 'status', '?')} for "
                    "reconcile_delivery. Not retried."
                )
            continue

        _close_job(OutboxJob, alias, job, "cutover_cancelled", now)
        detail = ""
        if job.kind == "invoices.sync" and job.payload.get("run_id"):
            updated = AutomationRun.objects.using(alias).filter(pk=job.payload["run_id"], status="RUNNING").update(
                status="FAILED", completed_at=now, error_summary=CUTOVER_SUMMARY
            )
            detail = f"; sync run {job.payload['run_id']} marked FAILED" if updated else ""
        report.cutover_actions.append(f"{job.status} job {job.pk} ({job.kind}) cancelled (cutover_cancelled){detail}.")

    for run in AutomationRun.objects.using(alias).filter(status="RUNNING"):
        AutomationRun.objects.using(alias).filter(pk=run.pk).update(
            status="FAILED", completed_at=now, error_summary=f"Abandoned: {CUTOVER_SUMMARY}"
        )
        steps = WorkflowStep.objects.using(alias).filter(run_id=run.pk)
        steps.filter(status="RUNNING").update(status="FAILED", completed_at=now)
        steps.filter(status="PENDING").update(status="SKIPPED")
        report.cutover_actions.append(f"{run.kind} run {run.pk} was RUNNING: marked FAILED (abandoned).")

    released = Lease.objects.using(alias).exclude(owner="").update(owner="", expires_at=None)
    if released:
        report.cutover_actions.append("Automation lease released.")

    unresolved = FollowUp.objects.using(alias).filter(status__in=["SENDING", "DELIVERY_UNKNOWN"]).count()
    if unresolved:
        report.cutover_actions.append(
            f"{unresolved} reminder(s) in SENDING or DELIVERY_UNKNOWN copied unchanged: reconcile them with "
            "`manage.py reconcile_delivery` after checking the sender's Gmail."
        )


def _close_job(OutboxJob: Any, alias: str, job: Any, code: str, now: dt.datetime) -> None:
    OutboxJob.objects.using(alias).filter(pk=job.pk).update(
        status="FAILED", last_error_code=code, locked_by="", locked_at=None, completed_at=now
    )
