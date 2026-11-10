"""Read-side queries used by the dashboard and by the Airflow pipeline."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Max, OuterRef, QuerySet, Subquery, Sum
from django.utils import timezone

from analytics.domain.errors import RepositoryNotFound
from analytics.models import DailyReport, Repository, RepositorySnapshot

REPOSITORY_STATUS_FILTERS = ("active", "inactive", "all")


def repositories_with_latest_snapshot() -> QuerySet[Repository]:
    """Repositories annotated with their most recent snapshot (all `None` when there is none).

    `last_synced_at` is the time of the last successful collection for the repository.
    """
    latest = RepositorySnapshot.objects.filter(repository=OuterRef("pk")).order_by("-snapshot_date")
    return Repository.objects.annotate(
        latest_snapshot_date=Subquery(latest.values("snapshot_date")[:1]),
        latest_stars=Subquery(latest.values("stars")[:1]),
        latest_forks=Subquery(latest.values("forks")[:1]),
        latest_open_issues=Subquery(latest.values("open_issues")[:1]),
        last_synced_at=Subquery(latest.values("collected_at")[:1]),
    )


def list_repositories(status: str = "active") -> QuerySet[Repository]:
    queryset = repositories_with_latest_snapshot().order_by("owner", "name")
    if status == "active":
        return queryset.filter(is_active=True)
    if status == "inactive":
        return queryset.filter(is_active=False)
    return queryset


def get_repository(repository_id: int) -> Repository:
    repository = repositories_with_latest_snapshot().filter(pk=repository_id).first()
    if repository is None:
        raise RepositoryNotFound()
    return repository


def list_active_repositories() -> QuerySet[Repository]:
    """The work list for one pipeline run."""
    return Repository.objects.filter(is_active=True).order_by("id")


def list_snapshots(repository_id: int, *, days: int) -> QuerySet[RepositorySnapshot]:
    """Snapshots of the last `days` days, oldest first. Works for removed repositories too."""
    if not Repository.objects.filter(pk=repository_id).exists():
        raise RepositoryNotFound()
    since = timezone.now().date() - timedelta(days=days - 1)
    return RepositorySnapshot.objects.filter(
        repository_id=repository_id, snapshot_date__gte=since
    ).order_by("snapshot_date")


def list_reports(*, limit: int) -> QuerySet[DailyReport]:
    return DailyReport.objects.order_by("-snapshot_date")[:limit]


def dashboard_summary() -> dict[str, Any]:
    """Totals over the latest snapshot of every active repository."""
    # Aggregate aliases must differ from the annotation names they aggregate over.
    totals = (
        repositories_with_latest_snapshot()
        .filter(is_active=True)
        .aggregate(
            repositories=Count("pk"),
            with_data=Count("latest_snapshot_date"),
            stars=Sum("latest_stars"),
            forks=Sum("latest_forks"),
            open_issues=Sum("latest_open_issues"),
            newest_sync=Max("last_synced_at"),
        )
    )
    return {
        "active_repositories": totals["repositories"],
        "repositories_with_data": totals["with_data"],
        "total_stars": totals["stars"] or 0,
        "total_forks": totals["forks"] or 0,
        "total_open_issues": totals["open_issues"] or 0,
        "last_synced_at": totals["newest_sync"],
    }


def pipeline_status() -> dict[str, Any]:
    """What the data says about the pipeline: the latest collection and the latest report.

    This is derived from stored snapshots and reports. The state of the Airflow DAG run itself
    lives in Airflow and is intentionally not copied here.
    """
    latest = RepositorySnapshot.objects.aggregate(
        last_collected_at=Max("collected_at"), latest_snapshot_date=Max("snapshot_date")
    )
    latest_date = latest["latest_snapshot_date"]
    collected = (
        RepositorySnapshot.objects.filter(
            snapshot_date=latest_date, repository__is_active=True
        ).count()
        if latest_date
        else 0
    )
    return {
        "collection": {
            "last_collected_at": latest["last_collected_at"],
            "latest_snapshot_date": latest_date,
            "repositories_collected": collected,
            "active_repositories": Repository.objects.filter(is_active=True).count(),
        },
        "latest_report": DailyReport.objects.order_by("-snapshot_date").first(),
    }
