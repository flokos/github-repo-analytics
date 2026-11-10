"""Requesting a daily report through the internal API: acceptance, idempotency and dispatch."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from kombu.exceptions import OperationalError as BrokerUnavailable

from analytics import tasks
from analytics.domain.errors import ReportDispatchFailed
from analytics.models import DailyReport
from analytics.tasks import dispatch_report_generation, generate_daily_report

from .helpers import make_repository, make_snapshot

pytestmark = pytest.mark.django_db

REPORTS = "/api/internal/reports/"
DISPATCHER = "analytics.api.internal_views.dispatch_report_generation"


@pytest.fixture
def today():
    return timezone.now().date()


@pytest.fixture
def collected(today):
    """One repository with a snapshot for yesterday and today."""
    repository = make_repository()
    make_snapshot(repository, today - timedelta(days=1), stars=100)
    make_snapshot(repository, today, stars=110)
    return repository


@pytest.fixture
def dispatched(monkeypatch):
    """Records what would have been published to the broker instead of publishing it."""
    calls = []
    monkeypatch.setattr(DISPATCHER, lambda report_id, task_id: calls.append((report_id, task_id)))
    return calls


def request_report(client, snapshot_date, run_id="scheduled__run-1"):
    body = {"snapshot_date": snapshot_date.isoformat(), "dag_run_id": run_id}
    return client.post(REPORTS, body, format="json")


def set_report(**fields) -> DailyReport:
    DailyReport.objects.update(**fields)
    return DailyReport.objects.get()


# --- Acceptance -----------------------------------------------------------------------------------
def test_requires_the_internal_token(api_client, collected, today, dispatched):
    response = request_report(api_client, today)

    assert response.status_code == 401
    assert dispatched == []
    assert not DailyReport.objects.exists()


def test_accepts_the_request_and_queues_generation(internal_client, collected, today, dispatched):
    response = request_report(internal_client, today)

    report = DailyReport.objects.get()
    assert response.status_code == 202
    assert response.json() == {
        "id": report.pk,
        "snapshot_date": today.isoformat(),
        "status": "queued",
        "enqueued": True,
    }
    assert report.status == "queued"
    assert report.trigger_run_id == "scheduled__run-1"
    assert report.attempts == 0
    # Exactly one message, carrying the task id that was stored with the row.
    assert dispatched == [(report.pk, report.celery_task_id)]
    assert report.celery_task_id


def test_a_queued_report_is_visible_to_the_dashboard(
    internal_client, api_client, collected, today, dispatched
):
    request_report(internal_client, today)

    listed = api_client.get("/api/reports/").json()
    status = api_client.get("/api/pipeline/status/").json()

    assert [(item["snapshot_date"], item["status"]) for item in listed] == [
        (today.isoformat(), "queued")
    ]
    assert status["latest_report"]["status"] == "queued"


def test_refuses_a_date_without_snapshots(internal_client, collected, today, dispatched):
    response = request_report(internal_client, today - timedelta(days=30))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_snapshots_for_date"
    assert dispatched == []
    assert not DailyReport.objects.exists()


@pytest.mark.parametrize("body", [{}, {"snapshot_date": "tomorrow"}, {"snapshot_date": None}])
def test_rejects_an_invalid_request(internal_client, dispatched, body):
    response = internal_client.post(REPORTS, body, format="json")

    assert response.status_code == 400
    assert "snapshot_date" in response.json()["error"]["details"]["fields"]
    assert dispatched == []


def test_the_run_id_is_optional(internal_client, collected, today, dispatched):
    response = internal_client.post(REPORTS, {"snapshot_date": today.isoformat()}, format="json")

    assert response.status_code == 202
    assert DailyReport.objects.get().trigger_run_id == ""


# --- Idempotency ----------------------------------------------------------------------------------
def test_repeating_the_request_while_queued_does_not_queue_it_twice(
    internal_client, collected, today, dispatched
):
    first = request_report(internal_client, today)
    second = request_report(internal_client, today)
    third = request_report(internal_client, today, run_id="manual__another-run")

    assert first.json()["enqueued"] is True
    assert (second.status_code, second.json()["enqueued"]) == (202, False)
    assert (third.status_code, third.json()["enqueued"]) == (202, False)
    assert second.json()["id"] == first.json()["id"]
    assert len(dispatched) == 1
    assert DailyReport.objects.count() == 1


def test_a_running_report_is_not_queued_again(internal_client, collected, today, dispatched):
    request_report(internal_client, today)
    set_report(status="running", started_at=timezone.now())

    response = request_report(internal_client, today, run_id="manual__another-run")

    assert response.json() == {
        "id": DailyReport.objects.get().pk,
        "snapshot_date": today.isoformat(),
        "status": "running",
        "enqueued": False,
    }
    assert len(dispatched) == 1


def test_a_retried_request_from_the_same_run_does_not_regenerate_a_finished_report(
    internal_client, collected, today, dispatched
):
    """Airflow retries the task after a timeout although Django had already done the work."""
    request_report(internal_client, today)
    set_report(status="completed", finished_at=timezone.now())

    response = request_report(internal_client, today)

    assert (response.json()["status"], response.json()["enqueued"]) == ("completed", False)
    assert len(dispatched) == 1


def test_a_later_run_regenerates_a_finished_report_with_a_new_task(
    internal_client, collected, today, dispatched
):
    """A second run on the same day collected newer snapshots, so the report is refreshed."""
    request_report(internal_client, today)
    finished = set_report(
        status="completed", finished_at=timezone.now(), attempts=1, total_stars=110
    )

    response = request_report(internal_client, today, run_id="manual__second-run")

    report = DailyReport.objects.get()
    assert (response.json()["status"], response.json()["enqueued"]) == ("queued", True)
    assert report.pk == finished.pk  # still one row per date
    assert report.trigger_run_id == "manual__second-run"
    assert report.celery_task_id != finished.celery_task_id
    assert report.attempts == 0
    assert (report.started_at, report.finished_at) == (None, None)
    assert report.total_stars == 110  # the previous results stay until they are replaced
    assert dispatched[1] == (report.pk, report.celery_task_id)


def test_a_failed_report_can_be_requested_again_even_by_the_same_run(
    internal_client, collected, today, dispatched
):
    request_report(internal_client, today)
    set_report(status="failed", error_message="boom", finished_at=timezone.now())

    response = request_report(internal_client, today)

    report = DailyReport.objects.get()
    assert (response.json()["status"], response.json()["enqueued"]) == ("queued", True)
    assert report.error_message == ""
    assert len(dispatched) == 2


@pytest.mark.parametrize("stuck_status", ["queued", "running"])
def test_a_report_stuck_in_flight_is_queued_again(
    internal_client, collected, today, dispatched, settings, stuck_status
):
    """For example when its message was lost because Redis restarted."""
    settings.REPORT_STALE_AFTER_SECONDS = 900
    request_report(internal_client, today)
    long_ago = timezone.now() - timedelta(minutes=20)
    set_report(
        status=stuck_status,
        requested_at=long_ago,
        started_at=long_ago if stuck_status == "running" else None,
    )

    response = request_report(internal_client, today)

    assert (response.json()["status"], response.json()["enqueued"]) == ("queued", True)
    assert len(dispatched) == 2


def test_the_database_allows_only_one_report_per_date(today):
    DailyReport.objects.create(snapshot_date=today, requested_at=timezone.now())

    with pytest.raises(IntegrityError), transaction.atomic():
        DailyReport.objects.create(snapshot_date=today, requested_at=timezone.now())


# --- Dispatch failures ----------------------------------------------------------------------------
def test_unreachable_broker_is_a_temporary_error_and_the_report_is_not_left_queued(
    internal_client, collected, today, monkeypatch
):
    def broker_down(report_id, task_id):
        raise ReportDispatchFailed()

    monkeypatch.setattr(DISPATCHER, broker_down)

    response = request_report(internal_client, today)

    report = DailyReport.objects.get()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "report_dispatch_failed"
    # "queued" with no message behind it would be a lie; "failed" lets a retry queue it again.
    assert report.status == "failed"
    assert "broker" in report.error_message


def test_request_succeeds_once_the_broker_is_back(internal_client, collected, today, monkeypatch):
    def broker_down(report_id, task_id):
        raise ReportDispatchFailed()

    monkeypatch.setattr(DISPATCHER, broker_down)
    request_report(internal_client, today)
    calls = []
    monkeypatch.setattr(DISPATCHER, lambda report_id, task_id: calls.append(task_id))

    response = request_report(internal_client, today)

    assert response.status_code == 202
    assert DailyReport.objects.get().status == "queued"
    assert len(calls) == 1


def test_dispatcher_publishes_the_task_under_the_given_id(monkeypatch):
    published = {}
    monkeypatch.setattr(
        generate_daily_report, "apply_async", lambda **options: published.update(options)
    )

    dispatch_report_generation(42, "chosen-task-id")

    assert published["args"] == [42]
    assert published["task_id"] == "chosen-task-id"
    assert published["retry_policy"]["max_retries"] == 2  # give up quickly, do not hang


@pytest.mark.parametrize("failure", [BrokerUnavailable("redis is down"), ConnectionRefusedError()])
def test_dispatcher_turns_broker_errors_into_a_dispatch_failure(monkeypatch, failure):
    def unreachable(**options):
        raise failure

    monkeypatch.setattr(generate_daily_report, "apply_async", unreachable)

    with pytest.raises(ReportDispatchFailed):
        dispatch_report_generation(42, "chosen-task-id")


# --- Request to finished report -------------------------------------------------------------------
def test_request_then_worker_run_produces_a_completed_report(
    internal_client, api_client, collected, today, monkeypatch
):
    """The whole Django side: accept the request, run the queued task, read the result."""

    def run_now(report_id, task_id):
        tasks.generate_daily_report.apply(args=[report_id], task_id=task_id)

    monkeypatch.setattr(DISPATCHER, run_now)

    response = request_report(internal_client, today)

    assert response.status_code == 202
    (report,) = api_client.get("/api/reports/").json()
    assert report["status"] == "completed"
    assert report["repository_count"] == 1
    assert report["total_stars"] == 110
    assert report["stars_delta"] == 10
    assert report["repository_changes"][0]["full_name"] == "django/django"
    assert report["repository_changes"][0]["stars_delta"] == 10
    assert report["attempts"] == 1
