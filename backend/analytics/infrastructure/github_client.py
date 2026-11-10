"""HTTP adapter for the GitHub REST API: the only backend module that talks to GitHub.

It implements `analytics.application.ports.GitHubGateway` and translates HTTP outcomes into the
errors defined in `analytics.domain.errors`, so no caller needs to know about status codes.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote, urlsplit

import requests
from django.conf import settings

from analytics.application.ports import RepositoryMetadata
from analytics.domain import errors
from analytics.domain.repository_url import RepositoryRef, is_valid_name, is_valid_owner

logger = logging.getLogger(__name__)

_REDIRECT_STATUSES = frozenset({301, 302, 307, 308})
_MAX_RETRY_AFTER_SECONDS = 3600


class GitHubApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_version: str,
        token: str = "",
        timeout: tuple[float, float] = (3.05, 8.0),
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._host = urlsplit(self._base_url).hostname
        self._timeout = timeout
        self._session = session or requests.Session()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": api_version,
            "User-Agent": "github-repo-analytics",
        }
        if token:
            # Never logged: only the path, status and remaining quota are.
            self._headers["Authorization"] = f"Bearer {token}"

    def get_repository(self, ref: RepositoryRef) -> RepositoryMetadata:
        # The request URL is assembled from the fixed base URL and two validated identifiers.
        path = f"/repos/{quote(ref.owner, safe='')}/{quote(ref.name, safe='')}"
        response = self._get(self._base_url + path)

        # GitHub answers 301 for renamed or transferred repositories. Follow at most one hop, and
        # only if it stays on the GitHub API host.
        if response.status_code in _REDIRECT_STATUSES:
            response = self._get(self._trusted_redirect_target(response))

        logger.info(
            "GitHub GET %s -> %s (rate limit remaining: %s)",
            path,
            response.status_code,
            response.headers.get("x-ratelimit-remaining", "unknown"),
        )
        self._raise_for_status(response)
        return self._parse_repository(response)

    def _get(self, url: str) -> requests.Response:
        try:
            return self._session.get(
                url, headers=self._headers, timeout=self._timeout, allow_redirects=False
            )
        except requests.Timeout as exc:
            raise errors.GitHubTimeout() from exc
        except requests.RequestException as exc:
            raise errors.GitHubUnavailable() from exc

    def _trusted_redirect_target(self, response: requests.Response) -> str:
        target = response.headers.get("Location", "")
        parts = urlsplit(target)
        if parts.scheme != "https" or parts.hostname != self._host:
            logger.warning("Refusing GitHub redirect to an unexpected location")
            raise errors.GitHubUnavailable()
        return target

    def _raise_for_status(self, response: requests.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        if status in (403, 429) and self._is_rate_limited(response):
            raise errors.GitHubRateLimited(retry_after=self._retry_after(response))
        if status == 404:
            raise errors.RepositoryUnavailable()
        if status in (403, 451):
            raise errors.RepositoryAccessDenied()
        if status == 401:
            # The configured GITHUB_TOKEN was rejected: an operator problem, not a user error.
            logger.error("GitHub rejected the configured API token")
        raise errors.GitHubUnavailable()

    @staticmethod
    def _is_rate_limited(response: requests.Response) -> bool:
        return (
            response.status_code == 429
            or response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> int | None:
        """Seconds until GitHub accepts requests again, when it told us."""
        try:
            if "retry-after" in response.headers:
                seconds = int(response.headers["retry-after"])
            elif "x-ratelimit-reset" in response.headers:
                seconds = int(response.headers["x-ratelimit-reset"]) - int(time.time())
            else:
                return None
        except ValueError:
            return None
        return max(1, min(seconds, _MAX_RETRY_AFTER_SECONDS))

    @staticmethod
    def _parse_repository(response: requests.Response) -> RepositoryMetadata:
        try:
            payload = response.json()
            github_id = payload["id"]
            owner = payload["owner"]["login"]
            name = payload["name"]
            is_private = payload["private"]
            # Missing visibility is treated as "not public": the repository is then refused.
            visibility = payload.get("visibility", "")
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("GitHub returned a repository payload in an unexpected shape")
            raise errors.GitHubUnavailable() from exc

        well_formed = (
            isinstance(github_id, int)
            and not isinstance(github_id, bool)
            and github_id > 0
            and isinstance(owner, str)
            and is_valid_owner(owner)
            and isinstance(name, str)
            and is_valid_name(name)
            and isinstance(is_private, bool)
            and isinstance(visibility, str)
        )
        if not well_formed:
            logger.error("GitHub returned repository metadata that failed validation")
            raise errors.GitHubUnavailable()

        return RepositoryMetadata(
            github_id=github_id,
            owner=owner,
            name=name,
            is_private=is_private,
            visibility=visibility,
        )


def build_github_gateway() -> GitHubApiClient:
    """The gateway used in production, configured from Django settings."""
    return GitHubApiClient(
        base_url=settings.GITHUB_API_BASE_URL,
        api_version=settings.GITHUB_API_VERSION,
        token=settings.GITHUB_TOKEN,
        timeout=(settings.GITHUB_CONNECT_TIMEOUT_SECONDS, settings.GITHUB_READ_TIMEOUT_SECONDS),
    )
