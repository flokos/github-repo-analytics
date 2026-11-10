"""Builders shared by the test modules."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from analytics.models import Repository, RepositorySnapshot

INTERNAL_TOKEN = "test-internal-token"
GITHUB_API = "https://api.github.com"


def github_url(owner: str, name: str) -> str:
    return f"{GITHUB_API}/repos/{owner}/{name}"


def github_payload(
    *,
    github_id: int = 4164482,
    owner: str = "django",
    name: str = "django",
    private: bool = False,
    visibility: str = "public",
) -> dict:
    """The subset of GitHub's "get a repository" response that the backend reads."""
    return {
        "id": github_id,
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "private": private,
        "visibility": visibility,
        "stargazers_count": 91701,
        "forks_count": 35213,
        "open_issues_count": 533,
    }


def make_repository(
    owner: str = "django", name: str = "django", *, github_id: int = 4164482, **fields
) -> Repository:
    return Repository.objects.create(owner=owner, name=name, github_id=github_id, **fields)


def make_snapshot(
    repository: Repository,
    snapshot_date: date,
    *,
    stars: int = 100,
    forks: int = 10,
    open_issues: int = 5,
    collected_at: datetime | None = None,
) -> RepositorySnapshot:
    return RepositorySnapshot.objects.create(
        repository=repository,
        snapshot_date=snapshot_date,
        stars=stars,
        forks=forks,
        open_issues=open_issues,
        collected_at=collected_at or datetime.combine(snapshot_date, time(6, 0), tzinfo=UTC),
    )
