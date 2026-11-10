"""Metric validation is pure logic: every rule has a case, and none needs Airflow."""

from datetime import UTC, datetime, timedelta

import pytest
from helpers import DJANGO

from github_analytics.errors import InvalidMetricsError, PermanentError
from github_analytics.validation import validate_metrics

NOW = datetime(2026, 3, 14, 6, 0, 30, tzinfo=UTC)


def metrics(**overrides) -> dict:
    return {
        "github_id": DJANGO["github_id"],
        "private": False,
        "visibility": "public",
        "stars": 1200,
        "forks": 340,
        "open_issues": 56,
        "collected_at": "2026-03-14T06:00:00+00:00",
        **overrides,
    }


def test_returns_the_snapshot_to_store():
    assert validate_metrics(DJANGO, metrics(), now=NOW) == {
        "repository_id": 1,
        "full_name": "django/django",
        "stars": 1200,
        "forks": 340,
        "open_issues": 56,
        "collected_at": "2026-03-14T06:00:00+00:00",
    }


def test_zero_counts_are_valid():
    snapshot = validate_metrics(DJANGO, metrics(stars=0, forks=0, open_issues=0), now=NOW)

    assert (snapshot["stars"], snapshot["forks"], snapshot["open_issues"]) == (0, 0, 0)


def test_collection_time_is_normalised_to_utc():
    snapshot = validate_metrics(DJANGO, metrics(collected_at="2026-03-14T08:00:00+02:00"), now=NOW)

    assert snapshot["collected_at"] == "2026-03-14T06:00:00+00:00"


def test_rejects_metrics_that_belong_to_a_different_repository():
    """Same owner/name, different GitHub id: the old name was reused after a rename."""
    with pytest.raises(InvalidMetricsError, match="renamed"):
        validate_metrics(DJANGO, metrics(github_id=999), now=NOW)


@pytest.mark.parametrize(
    "overrides",
    [
        {"private": True, "visibility": "private"},
        {"private": False, "visibility": "internal"},
        {"private": None},
        {"visibility": None},
    ],
)
def test_rejects_a_repository_that_is_no_longer_public(overrides):
    with pytest.raises(InvalidMetricsError, match="no longer public"):
        validate_metrics(DJANGO, metrics(**overrides), now=NOW)


@pytest.mark.parametrize("field", ["stars", "forks", "open_issues"])
@pytest.mark.parametrize("value", [-1, None, "12", 1.5, True, 2**31])
def test_rejects_counts_that_are_not_non_negative_integers(field, value):
    with pytest.raises(InvalidMetricsError, match=field):
        validate_metrics(DJANGO, metrics(**{field: value}), now=NOW)


@pytest.mark.parametrize("field", ["github_id", "stars", "forks", "open_issues", "collected_at"])
def test_rejects_missing_fields(field):
    incomplete = metrics()
    del incomplete[field]

    with pytest.raises(InvalidMetricsError):
        validate_metrics(DJANGO, incomplete, now=NOW)


@pytest.mark.parametrize("value", ["yesterday", "", None, 1741932000, "2026-03-14T06:00:00"])
def test_rejects_an_unusable_collection_time(value):
    with pytest.raises(InvalidMetricsError, match="collected_at"):
        validate_metrics(DJANGO, metrics(collected_at=value), now=NOW)


def test_rejects_a_collection_time_in_the_future():
    later = (NOW + timedelta(hours=1)).isoformat()

    with pytest.raises(InvalidMetricsError, match="future"):
        validate_metrics(DJANGO, metrics(collected_at=later), now=NOW)


def test_tolerates_small_clock_differences_between_machines():
    slightly_ahead = (NOW + timedelta(minutes=2)).isoformat()

    assert validate_metrics(DJANGO, metrics(collected_at=slightly_ahead), now=NOW)


def test_invalid_metrics_are_permanent_so_they_are_never_retried():
    assert issubclass(InvalidMetricsError, PermanentError)
