"""The Django Celery task that generates a daily report, executed in-process with `apply()`."""

from datetime import timedelta

import pytest
from django.db import OperationalError
from django.utils import timezone

from analytics import tasks
from analytics.application import reports
from analytics.models import DailyReport
from analytics.tasks import MAX_RETRIES, generate_daily_report, retry_delay

from .helpers import make_repository, make_snapshot

pytestmark = pytest.mark.django_db

TASK_ID = "11111111-1111-1111-1111-111111111111"


def queued_report(snapshot_date, *, task_id: str = TASK_ID, **fields) -> DailyReport:
    values = {"status": "queued", "requested_at": timezone.now(), "celery_task_id": task_id}
    return DailyReport.objects.create(snapshot_date=snapshot_date, **{**values, **fields})


def run_task(report: DailyReport, *, task_id: str = TASK_ID):
    """Run the task the way a worker would, with the task id the request stored on the report."""
    return generate_daily_report.apply(args=[report.pk], task_id=task_id)


@pytest.fixture
def today():
    return timezone.now().date()


@pytest.fixture
def collected(today):
    """Two repositories with history, plus one seen for the first time today."""
    django = make_repository("django", "django", github_id=1)
    make_snapshot(django, today - timedelta(days=1), stars=100, forks=10, open_issues=5)
    make_snapshot(django, today, stars=105, forks=12, open_issues=3)
    airflow = make_repository("apache", "airflow", github_id=2)
    make_snapshot(airflow, today - timedelta(days=7), stars=60, forks=5, open_issues=4)
    make_snapshot(airflow, today, stars=50, forks=5, open_issues=9)
    newcomer = make_repository("new", "arrival", github_id=3)
    make_snapshot(newcomer, today, stars=1000, forks=100, open_issues=10)
    return {"django": django, "airflow": airflow, "newcomer": newcomer}


# --- Successful generation ------------------------------------------------------------------------
def test_generates_the_report_from_the_days_snapshots(collected, today):
    report = queued_report(today)

    run_task(report)

    report.refresh_from_db()
    assert report.status == "completed"
    assert report.attempts == 1
    assert report.started_at is not None
    assert report.finished_at >= report.started_at
    assert report.error_message == ""
    assert report.repository_count == 3
    assert (report.total_stars, report.total_forks, report.total_open_issues) == (1155, 117, 22)
    # The newcomer adds to the totals but not to the change.
    assert (report.stars_delta, report.forks_delta, report.open_issues_delta) == (-5, 2, 3)
    assert report.repository_changes == [
        {
            "repository_id": collected["django"].pk,
            "full_name": "django/django",
            "stars": 105,
            "forks": 12,
            "open_issues": 3,
            "stars_delta": 5,
            "forks_delta": 2,
            "open_issues_delta": -2,
            "previous_snapshot_date": (today - timedelta(days=1)).isoformat(),
        },
        {
            "repository_id": collected["airflow"].pk,
            "full_name": "apache/airflow",
            "stars": 50,
            "forks": 5,
            "open_issues": 9,
            "stars_delta": -10,
            "forks_delta": 0,
            "open_issues_delta": 5,
            # The previous *available* snapshot, even though it is a week old.
            "previous_snapshot_date": (today - timedelta(days=7)).isoformat(),
        },
        {
            "repository_id": collected["newcomer"].pk,
            "full_name": "new/arrival",
            "stars": 1000,
            "forks": 100,
            "open_issues": 10,
            "stars_delta": None,
            "forks_delta": None,
            "open_issues_delta": None,
            "previous_snapshot_date": None,
        },
    ]


def test_only_snapshots_of_the_report_date_are_included(today):
    repository = make_repository()
    make_snapshot(repository, today - timedelta(days=2), stars=1)
    make_snapshot(repository, today - timedelta(days=1), stars=5)
    make_snapshot(repository, today, stars=9)  # later than the report date: must be ignored
    report = queued_report(today - timedelta(days=1))

    run_task(report)

    report.refresh_from_db()
    assert report.total_stars == 5
    assert report.stars_delta == 4


def test_a_repository_removed_after_collection_stays_in_that_days_report(today):
    removed = make_repository(is_active=False, deactivated_at=timezone.now())
    make_snapshot(removed, today, stars=7)
    report = queued_report(today)

    run_task(report)

    report.refresh_from_db()
    assert (report.repository_count, report.total_stars) == (1, 7)


def test_report_is_marked_running_while_it_is_being_generated(collected, today, monkeypatch):
    report = queued_report(today)
    seen = []
    real_build = reports.build_summary

    def spying_build(snapshot_date):
        seen.append(DailyReport.objects.get(pk=report.pk).status)
        return real_build(snapshot_date)

    monkeypatch.setattr(reports, "build_summary", spying_build)

    run_task(report)

    assert seen == ["running"]


# --- Deliveries that must not run -----------------------------------------------------------------
def test_duplicate_delivery_after_completion_does_not_generate_again(collected, today):
    report = queued_report(today)
    run_task(report)
    report.refresh_from_db()
    first_finish = report.finished_at

    run_task(report)

    report.refresh_from_db()
    assert report.status == "completed"
    assert report.attempts == 1
    assert report.finished_at == first_finish


def test_delivery_for_a_superseded_request_is_ignored(collected, today):
    """A newer request re-queued the report under another task id; the old message is stale."""
    report = queued_report(today, task_id="newer-task-id")

    run_task(report, task_id="older-task-id")

    report.refresh_from_db()
    assert report.status == "queued"
    assert report.attempts == 0


def test_redelivery_after_a_worker_crash_is_allowed_to_run_again(collected, today):
    """acks_late redelivers the message; the report still says "running" for the same task."""
    report = queued_report(today, status="running", started_at=timezone.now(), attempts=1)

    run_task(report)

    report.refresh_from_db()
    assert report.status == "completed"
    assert report.attempts == 2


def test_a_report_that_no_longer_exists_is_ignored():
    result = generate_daily_report.apply(args=[999_999], task_id=TASK_ID)

    assert result.successful()


# --- Failures and retries -------------------------------------------------------------------------
def test_unexpected_error_fails_the_report_without_retrying(collected, today, monkeypatch):
    report = queued_report(today)

    def broken(snapshot_date):
        raise ValueError("unexpected data")

    monkeypatch.setattr(reports, "build_summary", broken)

    result = run_task(report)

    report.refresh_from_db()
    assert result.failed()
    assert report.status == "failed"
    assert report.error_message == "ValueError: unexpected data"
    assert report.finished_at is not None
    assert report.attempts == 1


def test_temporary_database_error_is_retried_and_then_succeeds(collected, today, monkeypatch):
    report = queued_report(today)
    real_build = reports.build_summary
    real_start = reports.start_report
    attempts = iter([OperationalError("connection reset"), None])
    seen_at_start = []

    def flaky_build(snapshot_date):
        error = next(attempts)
        if error:
            raise error
        return real_build(snapshot_date)

    def spying_start(report_id, task_id):
        current = DailyReport.objects.get(pk=report_id)
        seen_at_start.append((current.status, current.error_message))
        return real_start(report_id, task_id)

    monkeypatch.setattr(reports, "build_summary", flaky_build)
    monkeypatch.setattr(reports, "start_report", spying_start)

    run_task(report)

    report.refresh_from_db()
    assert report.status == "completed"
    assert report.attempts == 2
    assert report.error_message == ""
    assert report.total_stars == 1155
    # Between the attempts the report was back in "queued", with the reason recorded.
    assert seen_at_start[0] == ("queued", "")
    assert seen_at_start[1][0] == "queued"
    assert "Attempt 1 failed and will be retried" in seen_at_start[1][1]


def test_gives_up_after_the_retry_limit(collected, today, monkeypatch):
    report = queued_report(today)
    calls = []

    def always_down(snapshot_date):
        calls.append(snapshot_date)
        raise OperationalError("database is unavailable")

    monkeypatch.setattr(reports, "build_summary", always_down)

    run_task(report)

    report.refresh_from_db()
    assert len(calls) == MAX_RETRIES + 1
    assert report.status == "failed"
    assert report.attempts == MAX_RETRIES + 1
    assert f"Gave up after {MAX_RETRIES + 1} attempts" in report.error_message
    assert "database is unavailable" in report.error_message


def test_retries_back_off_exponentially_up_to_a_cap():
    assert [retry_delay(retries) for retries in range(6)] == [5, 10, 20, 40, 60, 60]


def test_results_of_an_earlier_generation_survive_a_failed_one(collected, today, monkeypatch):
    report = queued_report(today)
    run_task(report)
    # A later run asks again and this time generation breaks.
    DailyReport.objects.filter(pk=report.pk).update(status="queued", celery_task_id="second-task")

    def broken(snapshot_date):
        raise ValueError("unexpected data")

    monkeypatch.setattr(reports, "build_summary", broken)

    run_task(report, task_id="second-task")

    report.refresh_from_db()
    assert report.status == "failed"
    assert report.total_stars == 1155
    assert len(report.repository_changes) == 3


def test_task_is_registered_under_a_stable_name_on_the_django_queue(settings):
    assert generate_daily_report.name == "analytics.generate_daily_report"
    assert tasks.generate_daily_report.app.conf.task_default_queue == "django_reports"
    assert generate_daily_report.acks_late is True
