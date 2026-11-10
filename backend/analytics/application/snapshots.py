"""Use case for recording a repository's daily metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from django.db import transaction

from analytics.domain.errors import RepositoryNotFound
from analytics.models import Repository, RepositorySnapshot


class SnapshotOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class SnapshotInput:
    repository_id: int
    stars: int
    forks: int
    open_issues: int
    collected_at: datetime  # timezone-aware


@dataclass(frozen=True, slots=True)
class RecordSnapshotResult:
    snapshot: RepositorySnapshot
    outcome: SnapshotOutcome


def record_snapshot(data: SnapshotInput) -> RecordSnapshotResult:
    """Store one observation, keeping at most one snapshot per repository per UTC day.

    The snapshot date is derived here, from the moment the metrics were observed. GitHub only
    exposes current values, so the date can never be chosen by the caller: re-running an old
    pipeline run records today's numbers under today's date instead of rewriting history.

    Repeating a call is harmless. A newer observation for the same day replaces the stored one;
    an identical or older one (a retry delivered late) changes nothing.
    """
    snapshot_date = data.collected_at.astimezone(UTC).date()
    values = {
        "stars": data.stars,
        "forks": data.forks,
        "open_issues": data.open_issues,
        "collected_at": data.collected_at,
    }

    with transaction.atomic():
        # Snapshots are accepted for inactive repositories too: a repository removed while a
        # pipeline run is in flight should not make that run fail.
        if not Repository.objects.filter(pk=data.repository_id).exists():
            raise RepositoryNotFound()

        # get_or_create relies on the unique constraint, so two concurrent writers cannot both
        # insert; the row lock then serialises the update below.
        snapshot, created = RepositorySnapshot.objects.select_for_update().get_or_create(
            repository_id=data.repository_id, snapshot_date=snapshot_date, defaults=values
        )
        if created:
            return RecordSnapshotResult(snapshot, SnapshotOutcome.CREATED)
        if data.collected_at <= snapshot.collected_at:
            return RecordSnapshotResult(snapshot, SnapshotOutcome.UNCHANGED)

        for field, value in values.items():
            setattr(snapshot, field, value)
        snapshot.save(update_fields=[*values, "updated_at"])
        return RecordSnapshotResult(snapshot, SnapshotOutcome.UPDATED)
