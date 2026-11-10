"""Interfaces the use cases need from the outside world."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from analytics.domain.repository_url import RepositoryRef


@dataclass(frozen=True, slots=True)
class RepositoryMetadata:
    """What GitHub says about a repository, reduced to the facts the use cases rely on."""

    github_id: int
    owner: str
    name: str
    is_private: bool
    visibility: str

    @property
    def is_public(self) -> bool:
        return not self.is_private and self.visibility == "public"


class GitHubGateway(Protocol):
    """Looks a repository up on GitHub.

    Implementations raise the GitHub-related errors from `analytics.domain.errors`.
    The real one is `analytics.infrastructure.github_client.GitHubApiClient`; tests pass a fake.
    """

    def get_repository(self, ref: RepositoryRef) -> RepositoryMetadata: ...
