"""Whole DAG runs, executed in-process with `dag.test()` against a throwaway metadata database.

These are slower than the unit tests (several seconds per run) but cover what unit tests cannot:
expansion over a list that only exists at run time, trigger rules, the retry policy as Airflow
applies it, and the final state of the run. GitHub and Django are mocked at the HTTP level.
"""

import json

import pytest
from airflow.dag_processing.dagbag import DagBag
from helpers import (
    ACTIVE_REPOSITORIES_URL,
    AIRFLOW,
    DAG_ID,
    DAGS_FOLDER,
    DJANGO,
    REPORTS_URL,
    SNAPSHOTS_URL,
    github_payload,
    github_url,
)

FETCH = "collect_repository.fetch_metrics"
VALIDATE = "collect_repository.validate_metrics"
PERSIST = "collect_repository.persist_snapshot"


@pytest.fixture(scope="module")
def dag():
    return DagBag(dag_folder=str(DAGS_FOLDER)).dags[DAG_ID]


def mock_django(http, repositories: list[dict]) -> None:
    """A Django that lists `repositories`, stores any snapshot and accepts any report request."""

    def store_snapshot(request):
        body = json.loads(request.body)
        stored = {
            "id": body["repository_id"],
            "repository_id": body["repository_id"],
            "snapshot_date": body["collected_at"][:10],
            "outcome": "created",
        }
        return 201, {}, json.dumps(stored)

    def accept_report(request):
        body = json.loads(request.body)
        accepted = {"id": 1, "snapshot_date": body["snapshot_date"], "status": "queued"}
        return 202, {}, json.dumps(accepted)

    http.get(ACTIVE_REPOSITORIES_URL, json=repositories)
    http.add_callback("POST", SNAPSHOTS_URL, callback=store_snapshot)
    http.add_callback("POST", REPORTS_URL, callback=accept_report)


def task_states(run) -> dict[tuple[str, int], str]:
    """{(task_id, map_index): state}; map_index is -1 for tasks that are not mapped."""
    return {(ti.task_id, ti.map_index): str(ti.state) for ti in run.get_task_instances()}


def requests_to(http, url: str) -> list[dict]:
    return [json.loads(call.request.body) for call in http.calls if call.request.url == url]


def test_collects_every_repository_then_requests_the_report(dag, http):
    mock_django(http, [DJANGO, AIRFLOW])
    http.get(github_url(DJANGO), json=github_payload(DJANGO, stargazers_count=91701))
    http.get(github_url(AIRFLOW), json=github_payload(AIRFLOW, stargazers_count=40000))

    run = dag.test()

    assert str(run.state) == "success"
    # One mapped instance of each per-repository task for each repository.
    assert task_states(run) == {
        ("load_active_repositories", -1): "success",
        (FETCH, 0): "success",
        (FETCH, 1): "success",
        (VALIDATE, 0): "success",
        (VALIDATE, 1): "success",
        (PERSIST, 0): "success",
        (PERSIST, 1): "success",
        ("trigger_daily_report", -1): "success",
    }
    # Mapped instances are labelled with the repository they handle.
    labels = {
        (ti.task_id, ti.rendered_map_index) for ti in run.get_task_instances() if ti.map_index >= 0
    }
    assert labels == {
        (task_id, name)
        for task_id in (FETCH, VALIDATE, PERSIST)
        for name in ("django/django", "apache/airflow")
    }

    snapshots = {body["repository_id"]: body for body in requests_to(http, SNAPSHOTS_URL)}
    assert snapshots[DJANGO["id"]]["stars"] == 91701
    assert snapshots[AIRFLOW["id"]]["stars"] == 40000
    assert set(snapshots[DJANGO["id"]]) == {
        "repository_id",
        "stars",
        "forks",
        "open_issues",
        "collected_at",
    }

    (report_request,) = requests_to(http, REPORTS_URL)
    assert report_request["snapshot_date"] == snapshots[DJANGO["id"]]["collected_at"][:10]
    assert report_request["dag_run_id"] == run.run_id


def test_a_single_repository_is_collected_and_reported(dag, http):
    """With exactly one mapped instance Airflow hands the report task a bare value, not a list."""
    mock_django(http, [DJANGO])
    http.get(github_url(DJANGO), json=github_payload(DJANGO))

    run = dag.test()

    assert str(run.state) == "success"
    assert task_states(run) == {
        ("load_active_repositories", -1): "success",
        (FETCH, 0): "success",
        (VALIDATE, 0): "success",
        (PERSIST, 0): "success",
        ("trigger_daily_report", -1): "success",
    }
    assert len(requests_to(http, REPORTS_URL)) == 1


def test_no_active_repositories_is_a_successful_run_that_does_nothing(dag, http):
    mock_django(http, [])

    run = dag.test()

    assert str(run.state) == "success"
    assert task_states(run) == {
        ("load_active_repositories", -1): "success",
        (FETCH, -1): "skipped",
        (VALIDATE, -1): "skipped",
        (PERSIST, -1): "skipped",
        ("trigger_daily_report", -1): "skipped",
    }
    assert [call.request.url for call in http.calls] == [ACTIVE_REPOSITORIES_URL]


def test_one_unavailable_repository_fails_the_run_but_not_its_siblings(dag, http):
    mock_django(http, [DJANGO, AIRFLOW])
    http.get(github_url(DJANGO), json=github_payload(DJANGO))
    http.get(github_url(AIRFLOW), status=404, json={"message": "Not Found"})

    run = dag.test()

    # Not silently green: the run is failed and no report is requested.
    assert str(run.state) == "failed"
    states = task_states(run)
    assert states[(FETCH, 0)] == "success"
    assert states[(VALIDATE, 0)] == "success"
    assert states[(PERSIST, 0)] == "success"
    assert states[(FETCH, 1)] == "failed"
    assert states[(VALIDATE, 1)] == "upstream_failed"
    assert states[(PERSIST, 1)] == "upstream_failed"
    assert states[("trigger_daily_report", -1)] == "upstream_failed"

    # The healthy repository was still stored.
    assert [body["repository_id"] for body in requests_to(http, SNAPSHOTS_URL)] == [DJANGO["id"]]
    assert requests_to(http, REPORTS_URL) == []

    # A 404 is permanent: one attempt, no retries, one GitHub request.
    failed = next(ti for ti in run.get_task_instances() if (ti.task_id, ti.map_index) == (FETCH, 1))
    assert failed.try_number == 1
    assert sum(call.request.url == github_url(AIRFLOW) for call in http.calls) == 1


def test_metrics_of_a_different_repository_under_the_same_name_are_not_stored(dag, http):
    """The tracked name now resolves to another GitHub repository (renamed, name reused)."""
    mock_django(http, [DJANGO])
    http.get(github_url(DJANGO), json=github_payload(DJANGO, id=999_999))

    run = dag.test()

    assert str(run.state) == "failed"
    states = task_states(run)
    assert states[(FETCH, 0)] == "success"
    assert states[(VALIDATE, 0)] == "failed"
    assert states[(PERSIST, 0)] == "upstream_failed"
    assert states[("trigger_daily_report", -1)] == "upstream_failed"
    assert requests_to(http, SNAPSHOTS_URL) == []


def test_rejected_internal_token_stops_the_run_before_github_is_called(dag, http):
    http.get(
        ACTIVE_REPOSITORIES_URL,
        status=401,
        json={"error": {"code": "authentication_failed", "message": "Invalid internal API token."}},
    )

    run = dag.test()

    assert str(run.state) == "failed"
    states = task_states(run)
    assert states[("load_active_repositories", -1)] == "failed"
    assert states[("trigger_daily_report", -1)] == "upstream_failed"
    assert [call.request.url for call in http.calls] == [ACTIVE_REPOSITORIES_URL]
