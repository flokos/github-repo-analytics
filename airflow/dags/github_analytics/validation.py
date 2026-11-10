"""Checks applied to fetched metrics before they are stored."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .errors import InvalidMetricsError

MAX_COUNT = 2_147_483_647  # the largest value the snapshot table's integer columns hold
ALLOWED_CLOCK_SKEW = timedelta(minutes=5)
COUNT_FIELDS = ("stars", "forks", "open_issues")


def validate_metrics(
    repository: dict[str, Any], metrics: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return the snapshot to store for `repository`, or raise `InvalidMetricsError`.

    The identity check matters most. Metrics are fetched by owner and name, and GitHub lets a
    renamed repository's old name be taken by someone else. Comparing GitHub's numeric id with
    the id recorded when the repository was added makes sure the numbers belong to the
    repository the user chose.
    """
    full_name = repository["full_name"]

    if metrics.get("github_id") != repository["github_id"]:
        raise InvalidMetricsError(
            f"GitHub returned repository id {metrics.get('github_id')!r} for {full_name}, but id "
            f"{repository['github_id']} is tracked. The repository was probably renamed and its "
            "old name reused. Remove it and add it again under its current address."
        )

    if metrics.get("private") is not False or metrics.get("visibility") != "public":
        raise InvalidMetricsError(f"{full_name} is no longer public; its metrics are not stored.")

    for field in COUNT_FIELDS:
        value = metrics.get(field)
        # bool is a subclass of int, so it has to be excluded explicitly.
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
            raise InvalidMetricsError(
                f"{full_name}: {field} must be a whole number between 0 and {MAX_COUNT}, "
                f"got {value!r}."
            )

    collected_at = _parse_timestamp(metrics.get("collected_at"), full_name)
    if collected_at > (now or datetime.now(UTC)) + ALLOWED_CLOCK_SKEW:
        raise InvalidMetricsError(f"{full_name}: collection time {collected_at} is in the future.")

    return {
        "repository_id": repository["id"],
        "full_name": full_name,
        "stars": metrics["stars"],
        "forks": metrics["forks"],
        "open_issues": metrics["open_issues"],
        "collected_at": collected_at.isoformat(),
    }


def _parse_timestamp(value: Any, full_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise InvalidMetricsError(
            f"{full_name}: collected_at is not an ISO 8601 timestamp: {value!r}."
        ) from None
    if parsed.tzinfo is None:
        raise InvalidMetricsError(f"{full_name}: collected_at has no timezone: {value!r}.")
    return parsed.astimezone(UTC)
