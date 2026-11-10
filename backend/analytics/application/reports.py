"""Use cases for the daily report: requesting it, and generating it.

Requesting happens inside an HTTP request from Airflow and only queues work. Generating happens
later in a Django Celery worker (`analytics.tasks`). The `DailyReport` row is the single source
of truth for where a report stands, so its status transitions are all made here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from analytics.domain.errors import NoSnapshotsForDate, ReportDispatchFailed
from analytics.domain.reports import (
    IN_FLIGHT,
    Metrics,
    ReportStatus,
    ReportSummary,
    SnapshotComparison,
    sources_of,
    summarize,
)
from analytics.models import DailyReport, RepositorySnapshot

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000

# (report id, task id) -> None. Raises ReportDispatchFailed when the broker cannot be reached.
Dispatch = Callable[[int, str], None]


@dataclass(frozen=True, slots=True)
class RequestReportResult:
    report: DailyReport
    enqueued: bool


# --- Requesting -----------------------------------------------------------------------------------
def request_daily_report(
    snapshot_date: date, *, run_id: str, dispatch: Dispatch
) -> RequestReportResult:
    """Queue generation of the report for `snapshot_date`. Safe to call repeatedly.

    There is one report row per date. What a repeated request does depends on where it stands:

    - queued or running: nothing; the work is already under way.
    - completed for this same `run_id`: nothing; this is a retry of a request that already worked.
    - completed for another run: generated again, because that run collected newer snapshots.
    - failed, or stuck in flight for longer than `REPORT_STALE_AFTER_SECONDS`: queued again.
    """
    if not RepositorySnapshot.objects.filter(snapshot_date=snapshot_date).exists():
        raise NoSnapshotsForDate()

    now = timezone.now()
    # The Celery task id is chosen here and stored with the row before the message is published,
    # so a worker can never start before the row knows which task owns it.
    task_id = str(uuid.uuid4())

    with transaction.atomic():
        report, created = DailyReport.objects.select_for_update().get_or_create(
            snapshot_date=snapshot_date,
            defaults={
                "status": ReportStatus.QUEUED,
                "requested_at": now,
                "trigger_run_id": run_id,
                "celery_task_id": task_id,
            },
        )
        if not created:
            if _is_in_flight(report, now) or _already_completed_for(report, run_id):
                return RequestReportResult(report=report, enqueued=False)
            _requeue(report, now=now, run_id=run_id, task_id=task_id)

    # Published only after the transaction has committed: the worker must be able to see the row.
    try:
        dispatch(report.pk, task_id)
    except ReportDispatchFailed:
        # Without this the row would claim "queued" with no message behind it.
        fail_report(report.pk, task_id, "The report could not be queued: broker unavailable.")
        raise

    logger.info("Report for %s queued (run %s)", snapshot_date, run_id or "unknown")
    return RequestReportResult(report=report, enqueued=True)


def _is_in_flight(report: DailyReport, now: datetime) -> bool:
    if report.status not in IN_FLIGHT:
        return False
    last_activity = max(report.requested_at, report.started_at or report.requested_at)
    stale_after = timedelta(seconds=settings.REPORT_STALE_AFTER_SECONDS)
    if now - last_activity < stale_after:
        return True
    logger.warning(
        "Report for %s was stuck in '%s'; queueing it again", report.snapshot_date, report.status
    )
    return False


def _already_completed_for(report: DailyReport, run_id: str) -> bool:
    return (
        report.status == ReportStatus.COMPLETED and bool(run_id) and report.trigger_run_id == run_id
    )


def _requeue(report: DailyReport, *, now: datetime, run_id: str, task_id: str) -> None:
    """Hand the report to a new task. Results of an earlier generation stay until replaced."""
    report.status = ReportStatus.QUEUED
    report.requested_at = now
    report.started_at = None
    report.finished_at = None
    report.trigger_run_id = run_id
    report.celery_task_id = task_id
    report.attempts = 0
    report.error_message = ""
    report.save()


# --- Generating -----------------------------------------------------------------------------------
def start_report(report_id: int, task_id: str) -> DailyReport | None:
    """Mark the report as running, or return `None` when this delivery must not run.

    A delivery is ignored when the report was deleted, when a newer request has taken over (the
    stored task id differs), or when the report already finished (a duplicate delivery).
    A report that is already "running" for the same task id is a redelivery after a worker
    crash, or a retry, and is allowed to start again.
    """
    with transaction.atomic():
        report = DailyReport.objects.select_for_update().filter(pk=report_id).first()
        startable = sources_of(ReportStatus.RUNNING) | {ReportStatus.RUNNING}
        if report is None or report.celery_task_id != task_id or report.status not in startable:
            return None
        report.status = ReportStatus.RUNNING
        report.started_at = timezone.now()
        report.attempts += 1
        report.save(update_fields=["status", "started_at", "attempts", "updated_at"])
        return report


def build_summary(snapshot_date: date) -> ReportSummary:
    """Compare every snapshot of `snapshot_date` with that repository's previous available one.

    "Previous available" means the most recent earlier snapshot, however old: a day on which
    the pipeline did not run does not break the comparison.
    """
    previous = RepositorySnapshot.objects.filter(
        repository=OuterRef("repository"), snapshot_date__lt=snapshot_date
    ).order_by("-snapshot_date")
    rows = (
        RepositorySnapshot.objects.filter(snapshot_date=snapshot_date)
        .select_related("repository")
        .annotate(
            previous_date=Subquery(previous.values("snapshot_date")[:1]),
            previous_stars=Subquery(previous.values("stars")[:1]),
            previous_forks=Subquery(previous.values("forks")[:1]),
            previous_open_issues=Subquery(previous.values("open_issues")[:1]),
        )
    )
    return summarize(
        SnapshotComparison(
            repository_id=row.repository_id,
            full_name=row.repository.full_name,
            current=Metrics(row.stars, row.forks, row.open_issues),
            previous=(
                Metrics(row.previous_stars, row.previous_forks, row.previous_open_issues)
                if row.previous_date is not None
                else None
            ),
            previous_snapshot_date=row.previous_date,
        )
        for row in rows
    )


def complete_report(report_id: int, task_id: str, summary: ReportSummary) -> bool:
    """Store the results. Returns False if the report is no longer this task's to finish."""
    now = timezone.now()
    updated = DailyReport.objects.filter(
        pk=report_id, celery_task_id=task_id, status__in=sources_of(ReportStatus.COMPLETED)
    ).update(
        status=ReportStatus.COMPLETED,
        finished_at=now,
        updated_at=now,
        error_message="",
        repository_count=summary.repository_count,
        total_stars=summary.total_stars,
        total_forks=summary.total_forks,
        total_open_issues=summary.total_open_issues,
        stars_delta=summary.stars_delta,
        forks_delta=summary.forks_delta,
        open_issues_delta=summary.open_issues_delta,
        repository_changes=[change.as_dict() for change in summary.changes],
    )
    return bool(updated)


def requeue_for_retry(report_id: int, task_id: str, message: str) -> None:
    """Running -> queued: the attempt failed for a temporary reason and will be retried."""
    DailyReport.objects.filter(
        pk=report_id, celery_task_id=task_id, status=ReportStatus.RUNNING
    ).update(
        status=ReportStatus.QUEUED,
        error_message=message[:MAX_ERROR_LENGTH],
        updated_at=timezone.now(),
    )


def fail_report(report_id: int, task_id: str, message: str) -> None:
    """Queued or running -> failed. The previous results, if any, are left in place."""
    now = timezone.now()
    DailyReport.objects.filter(
        pk=report_id, celery_task_id=task_id, status__in=sources_of(ReportStatus.FAILED)
    ).update(
        status=ReportStatus.FAILED,
        finished_at=now,
        updated_at=now,
        error_message=message[:MAX_ERROR_LENGTH],
    )
    logger.error("Report %s failed: %s", report_id, message)
