"""Report maths and the report lifecycle rules. Pure Python: no database, no Celery."""

from datetime import date

import pytest

from analytics.domain.reports import (
    Metrics,
    ReportStatus,
    SnapshotComparison,
    compare,
    sources_of,
    summarize,
)

YESTERDAY = date(2026, 3, 13)


def comparison(
    repository_id=1, full_name="django/django", current=(100, 10, 5), previous=None, since=None
) -> SnapshotComparison:
    return SnapshotComparison(
        repository_id=repository_id,
        full_name=full_name,
        current=Metrics(*current),
        previous=Metrics(*previous) if previous else None,
        previous_snapshot_date=since,
    )


# --- One repository -------------------------------------------------------------------------------
def test_change_is_current_minus_previous():
    change = compare(comparison(current=(105, 12, 3), previous=(100, 10, 5), since=YESTERDAY))

    assert (change.stars, change.forks, change.open_issues) == (105, 12, 3)
    assert (change.stars_delta, change.forks_delta, change.open_issues_delta) == (5, 2, -2)
    assert change.previous_snapshot_date == YESTERDAY


def test_first_snapshot_has_no_change_rather_than_a_change_from_zero():
    change = compare(comparison(current=(100, 10, 5)))

    assert (change.stars_delta, change.forks_delta, change.open_issues_delta) == (None, None, None)
    assert change.previous_snapshot_date is None


def test_unchanged_metrics_are_zero_not_missing():
    change = compare(comparison(current=(100, 10, 5), previous=(100, 10, 5), since=YESTERDAY))

    assert (change.stars_delta, change.forks_delta, change.open_issues_delta) == (0, 0, 0)


def test_metrics_can_go_down():
    change = compare(comparison(current=(90, 9, 0), previous=(100, 10, 5), since=YESTERDAY))

    assert (change.stars_delta, change.forks_delta, change.open_issues_delta) == (-10, -1, -5)


# --- The whole day --------------------------------------------------------------------------------
def test_summary_adds_up_totals_and_changes():
    summary = summarize(
        [
            comparison(1, "django/django", (105, 12, 3), (100, 10, 5), YESTERDAY),
            comparison(2, "apache/airflow", (50, 5, 9), (60, 5, 4), YESTERDAY),
        ]
    )

    assert summary.repository_count == 2
    assert (summary.total_stars, summary.total_forks, summary.total_open_issues) == (155, 17, 12)
    assert (summary.stars_delta, summary.forks_delta, summary.open_issues_delta) == (-5, 2, 3)


def test_a_newly_tracked_repository_counts_towards_totals_but_not_towards_change():
    """Otherwise adding a popular repository would look like thousands of stars gained in a day."""
    summary = summarize(
        [
            comparison(1, "django/django", (105, 12, 3), (100, 10, 5), YESTERDAY),
            comparison(2, "new/arrival", (90_000, 30_000, 500)),
        ]
    )

    assert summary.total_stars == 90_105
    assert (summary.stars_delta, summary.forks_delta, summary.open_issues_delta) == (5, 2, -2)


def test_lines_are_ordered_by_stars_gained_with_new_repositories_last():
    summary = summarize(
        [
            comparison(1, "small/gain", (11, 0, 0), (10, 0, 0), YESTERDAY),
            comparison(2, "new/arrival", (9999, 0, 0)),
            comparison(3, "big/gain", (150, 0, 0), (100, 0, 0), YESTERDAY),
            comparison(4, "lost/stars", (95, 0, 0), (100, 0, 0), YESTERDAY),
            comparison(5, "Another/new", (1, 0, 0)),
        ]
    )

    assert [change.full_name for change in summary.changes] == [
        "big/gain",
        "small/gain",
        "lost/stars",
        "Another/new",
        "new/arrival",
    ]


def test_a_day_without_snapshots_summarises_to_zeros():
    summary = summarize([])

    assert summary.repository_count == 0
    assert (summary.total_stars, summary.stars_delta) == (0, 0)
    assert summary.changes == ()


def test_lines_serialise_to_plain_json_values():
    with_history = compare(comparison(current=(105, 12, 3), previous=(100, 10, 5), since=YESTERDAY))
    without_history = compare(comparison(repository_id=2, full_name="new/arrival"))

    assert with_history.as_dict() == {
        "repository_id": 1,
        "full_name": "django/django",
        "stars": 105,
        "forks": 12,
        "open_issues": 3,
        "stars_delta": 5,
        "forks_delta": 2,
        "open_issues_delta": -2,
        "previous_snapshot_date": "2026-03-13",
    }
    assert without_history.as_dict()["stars_delta"] is None
    assert without_history.as_dict()["previous_snapshot_date"] is None


# --- Lifecycle ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("queued", "running"),
        ("queued", "failed"),  # could not be dispatched
        ("running", "completed"),
        ("running", "failed"),
        ("running", "queued"),  # waiting for a retry
        ("completed", "queued"),  # requested again with newer data
        ("failed", "queued"),  # requested again
    ],
)
def test_allowed_status_transitions(current, target):
    assert ReportStatus(current) in sources_of(ReportStatus(target))


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("queued", "completed"),  # cannot finish without running
        ("completed", "running"),
        ("completed", "failed"),
        ("failed", "running"),
        ("failed", "completed"),
        ("queued", "queued"),
        ("completed", "completed"),
    ],
)
def test_forbidden_status_transitions(current, target):
    assert ReportStatus(current) not in sources_of(ReportStatus(target))


def test_only_a_running_report_can_complete_and_any_report_in_flight_can_fail():
    assert sources_of(ReportStatus.COMPLETED) == {ReportStatus.RUNNING}
    assert sources_of(ReportStatus.FAILED) == {ReportStatus.QUEUED, ReportStatus.RUNNING}
    assert sources_of(ReportStatus.RUNNING) == {ReportStatus.QUEUED}
