"""The four pipeline steps as plain functions, with HTTP mocked."""

import json
from datetime import UTC, datetime

import pytest
from helpers import (
    ACTIVE_REPOSITORIES_URL,
    AIRFLOW,
    DJANGO,
    DJANGO_API,
    GITHUB_API,
    REPORTS_URL,
    SNAPSHOTS_URL,
    github_payload,
    github_url,
)

from github_analytics import pipeline
from github_analytics.clients import GitHubClient, InternalApiClient

NOW = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)


def api() -> InternalApiClient:
    return InternalApiClient(DJANGO_API, token="internal-secret", timeout=(1, 1))


def github() -> GitHubClient:
    return GitHubClient(GITHUB_API, token="ghp_secret_value", timeout=(1, 1))


def test_step_1_returns_only_the_fields_the_pipeline_needs(http):
    """The list travels through XCom, so anything extra Django might add is dropped."""
    http.get(ACTIVE_REPOSITORIES_URL, json=[{**DJANGO, "description": "x" * 5000}, AIRFLOW])

    repositories = pipeline.load_active_repositories(api())

    assert repositories == [DJANGO, AIRFLOW]


def test_step_1_with_no_active_repositories(http):
    http.get(ACTIVE_REPOSITORIES_URL, json=[])

    assert pipeline.load_active_repositories(api()) == []


def test_step_2_reduces_the_github_response_to_the_metrics(http):
    http.get(
        github_url(DJANGO),
        json=github_payload(DJANGO, description="The Web framework", topics=["python"] * 20),
    )

    metrics = pipeline.fetch_metrics(github(), DJANGO, now=NOW)

    assert metrics == {
        "github_id": 4164482,
        "private": False,
        "visibility": "public",
        "stars": 1200,
        "forks": 340,
        "open_issues": 56,
        "collected_at": "2026-03-14T06:00:00+00:00",
    }


def test_step_2_never_returns_the_token(http):
    """Task return values are stored in XCom, which must not hold credentials."""
    http.get(github_url(DJANGO), json=github_payload(DJANGO))

    metrics = pipeline.fetch_metrics(github(), DJANGO, now=NOW)

    assert "ghp_secret_value" not in json.dumps(metrics)


def test_step_2_passes_missing_fields_on_as_none_for_validation_to_reject(http):
    payload = github_payload(DJANGO)
    del payload["stargazers_count"]
    http.get(github_url(DJANGO), json=payload)

    assert pipeline.fetch_metrics(github(), DJANGO, now=NOW)["stars"] is None


def test_step_3_sends_only_snapshot_fields_and_returns_a_small_result(http):
    http.post(
        SNAPSHOTS_URL,
        status=201,
        json={"id": 9, "repository_id": 1, "snapshot_date": "2026-03-14", "outcome": "created"},
    )
    snapshot = {
        "repository_id": 1,
        "full_name": "django/django",
        "stars": 1200,
        "forks": 340,
        "open_issues": 56,
        "collected_at": "2026-03-14T06:00:00+00:00",
    }

    result = pipeline.persist_snapshot(api(), snapshot)

    assert result == {"repository_id": 1, "snapshot_date": "2026-03-14", "outcome": "created"}
    assert json.loads(http.calls[0].request.body) == {
        "repository_id": 1,
        "stars": 1200,
        "forks": 340,
        "open_issues": 56,
        "collected_at": "2026-03-14T06:00:00+00:00",
    }


def test_step_4_requests_one_report_for_the_date_that_was_collected(http):
    http.post(
        REPORTS_URL, status=202, json={"id": 4, "snapshot_date": "2026-03-14", "status": "queued"}
    )
    results = [
        {"repository_id": 1, "snapshot_date": "2026-03-14", "outcome": "created"},
        {"repository_id": 2, "snapshot_date": "2026-03-14", "outcome": "updated"},
    ]

    reports = pipeline.request_daily_reports(api(), results, "manual__2026-03-14")

    assert reports == [{"report_id": 4, "snapshot_date": "2026-03-14", "status": "queued"}]
    assert len(http.calls) == 1
    assert json.loads(http.calls[0].request.body) == {
        "snapshot_date": "2026-03-14",
        "dag_run_id": "manual__2026-03-14",
    }


def test_step_4_covers_both_dates_when_a_run_straddles_midnight(http):
    def accept(request):
        date = json.loads(request.body)["snapshot_date"]
        return 202, {}, json.dumps({"id": 1, "snapshot_date": date, "status": "queued"})

    http.add_callback("POST", REPORTS_URL, callback=accept, content_type="application/json")
    results = [
        {"repository_id": 1, "snapshot_date": "2026-03-15", "outcome": "created"},
        {"repository_id": 2, "snapshot_date": "2026-03-14", "outcome": "created"},
    ]

    reports = pipeline.request_daily_reports(api(), results, "manual__x")

    assert [report["snapshot_date"] for report in reports] == ["2026-03-14", "2026-03-15"]


RESULT = {"repository_id": 1, "snapshot_date": "2026-03-14", "outcome": "created"}
OTHER = {"repository_id": 2, "snapshot_date": "2026-03-14", "outcome": "created"}


@pytest.mark.parametrize(
    ("handed_over", "expected"),
    [
        (None, []),  # no repositories: the mapped tasks were skipped
        (RESULT, [RESULT]),  # exactly one repository: Airflow passes the bare value
        ([RESULT, OTHER], [RESULT, OTHER]),  # several repositories: a list
        (iter([RESULT, OTHER]), [RESULT, OTHER]),  # ... or a lazy sequence
        ([], []),
    ],
)
def test_mapped_results_are_normalised_to_a_list(handed_over, expected):
    assert pipeline.as_result_list(handed_over) == expected
