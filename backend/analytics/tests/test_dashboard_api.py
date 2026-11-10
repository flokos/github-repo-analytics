"""Read endpoints behind the dashboard: history, summary, reports and pipeline status."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from analytics.models import DailyReport

from .helpers import make_repository, make_snapshot

pytestmark = pytest.mark.django_db


def make_report(snapshot_date, status="completed", **fields) -> DailyReport:
    return DailyReport.objects.create(
        snapshot_date=snapshot_date, status=status, requested_at=timezone.now(), **fields
    )


# --- Snapshot history -----------------------------------------------------------------------------
def test_history_is_returned_oldest_first(api_client):
    today = timezone.now().date()
    repository = make_repository()
    make_snapshot(repository, today, stars=30)
    make_snapshot(repository, today - timedelta(days=2), stars=10)
    make_snapshot(repository, today - timedelta(days=1), stars=20)

    response = api_client.get(f"/api/repositories/{repository.pk}/snapshots/")

    assert response.status_code == 200
    assert [item["stars"] for item in response.json()] == [10, 20, 30]
    assert set(response.json()[0]) == {
        "snapshot_date",
        "stars",
        "forks",
        "open_issues",
        "collected_at",
    }


def test_history_can_be_limited_to_recent_days(api_client):
    today = timezone.now().date()
    repository = make_repository()
    for age in (0, 1, 2, 10):
        make_snapshot(repository, today - timedelta(days=age), stars=age)

    response = api_client.get(f"/api/repositories/{repository.pk}/snapshots/", {"days": 3})

    assert [item["stars"] for item in response.json()] == [2, 1, 0]


def test_history_of_a_removed_repository_stays_available(api_client):
    repository = make_repository(is_active=False, deactivated_at=timezone.now())
    make_snapshot(repository, timezone.now().date())

    response = api_client.get(f"/api/repositories/{repository.pk}/snapshots/")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_history_of_an_unknown_repository_is_not_found(api_client):
    response = api_client.get("/api/repositories/999/snapshots/")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("days", ["0", "-1", "abc", "367", "1.5"])
def test_history_rejects_an_invalid_range(api_client, days):
    repository = make_repository()

    response = api_client.get(f"/api/repositories/{repository.pk}/snapshots/", {"days": days})

    assert response.status_code == 400
    assert "days" in response.json()["error"]["details"]["fields"]


# --- Summary --------------------------------------------------------------------------------------
def test_summary_adds_up_the_latest_snapshot_of_each_active_repository(api_client):
    today = timezone.now().date()
    django = make_repository("django", "django", github_id=1)
    make_snapshot(django, today - timedelta(days=1), stars=90, forks=9, open_issues=1)
    newest = make_snapshot(django, today, stars=100, forks=10, open_issues=2)
    airflow = make_repository("apache", "airflow", github_id=2)
    make_snapshot(airflow, today - timedelta(days=1), stars=50, forks=5, open_issues=3)
    make_repository("new", "repo", github_id=3)  # tracked, not collected yet
    removed = make_repository("gone", "away", github_id=4, is_active=False)
    make_snapshot(removed, today, stars=1000, forks=1000, open_issues=1000)

    response = api_client.get("/api/dashboard/summary/")

    assert response.status_code == 200
    assert response.json() == {
        "active_repositories": 3,
        "repositories_with_data": 2,
        "total_stars": 150,
        "total_forks": 15,
        "total_open_issues": 5,
        "last_synced_at": newest.collected_at.isoformat().replace("+00:00", "Z"),
    }


def test_summary_of_an_empty_system(api_client):
    assert api_client.get("/api/dashboard/summary/").json() == {
        "active_repositories": 0,
        "repositories_with_data": 0,
        "total_stars": 0,
        "total_forks": 0,
        "total_open_issues": 0,
        "last_synced_at": None,
    }


# --- Reports --------------------------------------------------------------------------------------
def test_reports_are_listed_newest_first(api_client):
    today = timezone.now().date()
    make_report(today - timedelta(days=1), total_stars=10)
    make_report(today, status="failed", error_message="boom")

    response = api_client.get("/api/reports/")

    assert response.status_code == 200
    body = response.json()
    assert [item["snapshot_date"] for item in body] == [
        today.isoformat(),
        (today - timedelta(days=1)).isoformat(),
    ]
    assert body[0]["status"] == "failed"
    assert body[0]["error_message"] == "boom"
    assert body[1]["total_stars"] == 10


def test_reports_can_be_limited(api_client):
    today = timezone.now().date()
    for age in range(5):
        make_report(today - timedelta(days=age))

    assert len(api_client.get("/api/reports/", {"limit": 2}).json()) == 2
    assert api_client.get("/api/reports/", {"limit": 0}).status_code == 400
    assert api_client.get("/api/reports/", {"limit": 101}).status_code == 400


# --- Pipeline status ------------------------------------------------------------------------------
def test_pipeline_status_before_anything_has_run(api_client):
    make_repository()

    response = api_client.get("/api/pipeline/status/")

    assert response.status_code == 200
    assert response.json() == {
        "collection": {
            "last_collected_at": None,
            "latest_snapshot_date": None,
            "repositories_collected": 0,
            "active_repositories": 1,
        },
        "latest_report": None,
    }


def test_pipeline_status_reports_the_latest_collection_and_report(api_client):
    today = timezone.now().date()
    collected_at = datetime(today.year, today.month, today.day, 0, 0, 5, tzinfo=UTC)
    django = make_repository("django", "django", github_id=1)
    make_snapshot(django, today - timedelta(days=1))
    make_snapshot(django, today, collected_at=collected_at)
    make_repository("apache", "airflow", github_id=2)  # active, missing from today's collection
    removed = make_repository("gone", "away", github_id=3, is_active=False)
    make_snapshot(removed, today)
    make_report(today - timedelta(days=1))
    make_report(today, status="running", attempts=1)

    body = api_client.get("/api/pipeline/status/").json()

    assert body["collection"]["latest_snapshot_date"] == today.isoformat()
    assert body["collection"]["repositories_collected"] == 1
    assert body["collection"]["active_repositories"] == 2
    assert body["collection"]["last_collected_at"] is not None
    assert body["latest_report"]["snapshot_date"] == today.isoformat()
    assert body["latest_report"]["status"] == "running"
    assert body["latest_report"]["attempts"] == 1
