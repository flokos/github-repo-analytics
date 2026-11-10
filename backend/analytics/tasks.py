"""Django Celery tasks.

Deliberately thin: the task handles Celery concerns (delivery, retries, time limits) and delegates
the work and every status change to `analytics.application.reports`.
"""

from __future__ import annotations

import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import InterfaceError, OperationalError
from kombu.exceptions import KombuError

from analytics.application import reports
from analytics.domain.errors import ReportDispatchFailed

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Database hiccups (connection dropped, failover, lock timeout) are worth another attempt.
# Anything else is a bug or bad data, and retrying would only repeat it.
TRANSIENT_ERRORS = (OperationalError, InterfaceError)


def retry_delay(retries_so_far: int) -> int:
    """Exponential backoff between attempts: 5 s, 10 s, 20 s (capped at 60 s)."""
    return min(5 * 2**retries_so_far, 60)


@shared_task(
    bind=True,
    name="analytics.generate_daily_report",
    max_retries=MAX_RETRIES,
    # Acknowledge only after the task has finished, so a worker that dies mid-run does not lose
    # the report: the broker redelivers the message. That makes "runs more than once" possible,
    # which is safe because generation is idempotent and guarded by the report's task id.
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_daily_report(self, report_id: int) -> None:
    task_id = self.request.id

    report = reports.start_report(report_id, task_id)
    if report is None:
        logger.info("Report %s: nothing to do for task %s", report_id, task_id)
        return

    attempt = self.request.retries + 1
    logger.info("Generating report for %s (attempt %d)", report.snapshot_date, attempt)
    try:
        summary = reports.build_summary(report.snapshot_date)
        reports.complete_report(report_id, task_id, summary)
    except TRANSIENT_ERRORS as exc:
        if self.request.retries >= self.max_retries:
            reports.fail_report(report_id, task_id, f"Gave up after {attempt} attempts: {exc}")
            raise
        reports.requeue_for_retry(
            report_id, task_id, f"Attempt {attempt} failed and will be retried: {exc}"
        )
        raise self.retry(exc=exc, countdown=retry_delay(self.request.retries)) from exc
    except SoftTimeLimitExceeded:
        reports.fail_report(report_id, task_id, "Generation exceeded its time limit.")
        raise
    except Exception as exc:
        reports.fail_report(report_id, task_id, f"{type(exc).__name__}: {exc}")
        raise

    logger.info(
        "Report for %s completed: %d repositories", report.snapshot_date, summary.repository_count
    )


def dispatch_report_generation(report_id: int, task_id: str) -> None:
    """Publish the task to the Django Celery queue under a task id chosen by the caller.

    Publishing retries briefly and then gives up, so an unreachable broker turns into a prompt
    error for the caller instead of a hanging HTTP request.
    """
    try:
        generate_daily_report.apply_async(
            args=[report_id],
            task_id=task_id,
            retry=True,
            retry_policy={
                "max_retries": 2,
                "interval_start": 0.2,
                "interval_step": 0.3,
                "interval_max": 1.0,
            },
        )
    except (KombuError, OSError) as exc:
        logger.error("Could not publish report task: %s", type(exc).__name__)
        raise ReportDispatchFailed() from exc
