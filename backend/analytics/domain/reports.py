"""Daily report lifecycle."""

from __future__ import annotations

from enum import StrEnum


class ReportStatus(StrEnum):
    QUEUED = "queued"  # requested; waiting for a Django Celery worker
    RUNNING = "running"  # a worker is generating it
    COMPLETED = "completed"  # results are stored
    FAILED = "failed"  # gave up; `error_message` says why


# A report may be requested again after it finished (new data for the same day) or failed, and a
# running report goes back to queued while it waits for a retry.
_ALLOWED_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.QUEUED: frozenset({ReportStatus.RUNNING, ReportStatus.FAILED}),
    ReportStatus.RUNNING: frozenset(
        {ReportStatus.COMPLETED, ReportStatus.FAILED, ReportStatus.QUEUED}
    ),
    ReportStatus.COMPLETED: frozenset({ReportStatus.QUEUED}),
    ReportStatus.FAILED: frozenset({ReportStatus.QUEUED}),
}

IN_FLIGHT = frozenset({ReportStatus.QUEUED, ReportStatus.RUNNING})


def can_transition(current: ReportStatus | str, target: ReportStatus | str) -> bool:
    return ReportStatus(target) in _ALLOWED_TRANSITIONS[ReportStatus(current)]
