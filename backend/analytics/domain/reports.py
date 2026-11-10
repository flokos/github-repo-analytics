"""Daily report rules: the lifecycle of a report and how its numbers are calculated."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


# --- Lifecycle ------------------------------------------------------------------------------------
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


def sources_of(target: ReportStatus) -> frozenset[ReportStatus]:
    """Every status a report may be in immediately before it becomes `target`.

    The use cases filter on this when they change a report's status, so a transition that is not
    in the table above cannot be written.
    """
    return frozenset(
        status for status, targets in _ALLOWED_TRANSITIONS.items() if target in targets
    )


# --- Calculation ----------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Metrics:
    stars: int
    forks: int
    open_issues: int


@dataclass(frozen=True, slots=True)
class SnapshotComparison:
    """One repository's metrics on the report date next to its previous available snapshot."""

    repository_id: int
    full_name: str
    current: Metrics
    previous: Metrics | None = None
    previous_snapshot_date: date | None = None


@dataclass(frozen=True, slots=True)
class RepositoryChange:
    """One line of the report. Deltas are `None` when there is nothing earlier to compare with."""

    repository_id: int
    full_name: str
    stars: int
    forks: int
    open_issues: int
    stars_delta: int | None
    forks_delta: int | None
    open_issues_delta: int | None
    previous_snapshot_date: date | None

    def as_dict(self) -> dict[str, Any]:
        previous = self.previous_snapshot_date
        return {
            "repository_id": self.repository_id,
            "full_name": self.full_name,
            "stars": self.stars,
            "forks": self.forks,
            "open_issues": self.open_issues,
            "stars_delta": self.stars_delta,
            "forks_delta": self.forks_delta,
            "open_issues_delta": self.open_issues_delta,
            "previous_snapshot_date": previous.isoformat() if previous else None,
        }


@dataclass(frozen=True, slots=True)
class ReportSummary:
    repository_count: int
    total_stars: int
    total_forks: int
    total_open_issues: int
    stars_delta: int
    forks_delta: int
    open_issues_delta: int
    changes: tuple[RepositoryChange, ...]


def compare(comparison: SnapshotComparison) -> RepositoryChange:
    current, previous = comparison.current, comparison.previous
    return RepositoryChange(
        repository_id=comparison.repository_id,
        full_name=comparison.full_name,
        stars=current.stars,
        forks=current.forks,
        open_issues=current.open_issues,
        stars_delta=current.stars - previous.stars if previous else None,
        forks_delta=current.forks - previous.forks if previous else None,
        open_issues_delta=current.open_issues - previous.open_issues if previous else None,
        previous_snapshot_date=comparison.previous_snapshot_date if previous else None,
    )


def summarize(comparisons: Iterable[SnapshotComparison]) -> ReportSummary:
    """Aggregate one day's snapshots into a report.

    Totals add up the current values of every repository in the report. The aggregate deltas add
    up only the repositories that have an earlier snapshot: a repository seen for the first time
    has no change yet, and counting its whole star total as "gained today" would be misleading.

    Lines are ordered by stars gained, largest first; repositories without a previous snapshot
    come last.
    """
    changes = [compare(comparison) for comparison in comparisons]
    changes.sort(
        key=lambda change: (
            change.stars_delta is None,
            -(change.stars_delta or 0),
            change.full_name.lower(),
        )
    )
    return ReportSummary(
        repository_count=len(changes),
        total_stars=sum(change.stars for change in changes),
        total_forks=sum(change.forks for change in changes),
        total_open_issues=sum(change.open_issues for change in changes),
        stars_delta=sum(change.stars_delta or 0 for change in changes),
        forks_delta=sum(change.forks_delta or 0 for change in changes),
        open_issues_delta=sum(change.open_issues_delta or 0 for change in changes),
        changes=tuple(changes),
    )
