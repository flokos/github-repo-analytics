"""The four steps of the pipeline as plain functions.

Each takes the client it needs as an argument, which is what makes it testable. Return values
are deliberately small: they are stored in XCom, and they never contain a token.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .clients import GitHubClient, InternalApiClient

logger = logging.getLogger(__name__)

REPOSITORY_FIELDS = ("id", "github_id", "owner", "name", "full_name")
SNAPSHOT_FIELDS = ("repository_id", "stars", "forks", "open_issues", "collected_at")


def load_active_repositories(api: InternalApiClient) -> list[dict[str, Any]]:
    """Step 1: the repositories to process in this run, as decided by Django at this moment."""
    repositories = [
        {field: repository[field] for field in REPOSITORY_FIELDS}
        for repository in api.list_active_repositories()
    ]
    logger.info("Django reports %d active repositories", len(repositories))
    return repositories


def fetch_metrics(
    github: GitHubClient, repository: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Step 2: read one repository's current metrics from GitHub."""
    payload = github.fetch_repository(repository["owner"], repository["name"])
    metrics = {
        "github_id": payload.get("id"),
        "private": payload.get("private"),
        "visibility": payload.get("visibility"),
        "stars": payload.get("stargazers_count"),
        "forks": payload.get("forks_count"),
        # GitHub's open_issues_count includes open pull requests.
        "open_issues": payload.get("open_issues_count"),
        "collected_at": (now or datetime.now(UTC)).isoformat(),
    }
    logger.info(
        "Fetched %s: stars=%s forks=%s open_issues=%s",
        repository["full_name"],
        metrics["stars"],
        metrics["forks"],
        metrics["open_issues"],
    )
    return metrics


def persist_snapshot(api: InternalApiClient, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Step 3 (after validation): store the snapshot through Django. Safe to repeat."""
    result = api.save_snapshot({field: snapshot[field] for field in SNAPSHOT_FIELDS})
    logger.info(
        "Snapshot for %s on %s: %s",
        snapshot["full_name"],
        result["snapshot_date"],
        result["outcome"],
    )
    return {
        "repository_id": result["repository_id"],
        "snapshot_date": result["snapshot_date"],
        "outcome": result["outcome"],
    }


def as_result_list(value: Any) -> list[dict[str, Any]]:
    """Normalise the per-repository results as Airflow hands them to a downstream task.

    A task that depends on mapped tasks receives a list when they expanded into several
    instances, the bare value when they expanded into exactly one, and `None` when they did not
    expand at all (no repositories, so the mapped tasks were skipped).
    """
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def request_daily_reports(
    api: InternalApiClient, results: list[dict[str, Any]], dag_run_id: str
) -> list[dict[str, Any]]:
    """Step 4: ask Django to generate the report for every date this run wrote snapshots for.

    That is one date, except for a run that happens to straddle midnight UTC.
    Django only queues the work: the report itself is produced by a Django Celery worker.
    """
    reports = []
    for snapshot_date in sorted({result["snapshot_date"] for result in results}):
        report = api.request_daily_report(snapshot_date, dag_run_id)
        logger.info(
            "Report for %s accepted by Django with status '%s'. It is generated asynchronously "
            "by the Django Celery worker; follow it at /api/reports/, not in this DAG run.",
            report["snapshot_date"],
            report["status"],
        )
        reports.append(
            {
                "report_id": report["id"],
                "snapshot_date": report["snapshot_date"],
                "status": report["status"],
            }
        )
    return reports
