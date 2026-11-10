"""HTTP clients used by the pipeline tasks.

Both translate HTTP outcomes into the errors in `errors.py`, so tasks never inspect status codes.
Neither logs request headers: tokens stay out of task logs.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from . import errors

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = (5.0, 15.0)  # (connect, read) seconds
GITHUB_API_VERSION = "2026-03-10"
USER_AGENT = "github-repo-analytics-pipeline"

# The fetch task retries after 30 s, 60 s and 120 s. A rate limit that resets later than that
# cannot be waited out by retrying, so it is reported as permanent instead of burning retries.
RETRYABLE_RATE_LIMIT_SECONDS = 240

_REDIRECT_STATUSES = frozenset({301, 302, 307, 308})


class GitHubClient:
    """Reads repository metrics from the GitHub REST API."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._host = urlsplit(self._base_url).hostname
        self._timeout = timeout
        self._session = session or requests.Session()
        self._headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def fetch_repository(self, owner: str, name: str) -> dict[str, Any]:
        """Return GitHub's description of one repository."""
        path = f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        response = self._get(self._base_url + path)

        # A renamed or transferred repository answers 301. Follow one hop, on the same host only.
        if response.status_code in _REDIRECT_STATUSES:
            response = self._get(self._trusted_redirect_target(response))

        remaining = response.headers.get("x-ratelimit-remaining", "unknown")
        logger.info(
            "GitHub GET %s -> %s (rate limit remaining: %s)",
            path,
            response.status_code,
            remaining,
        )
        self._raise_for_status(response, f"{owner}/{name}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise errors.GitHubUnavailableError("GitHub returned a body that is not JSON.") from exc
        if not isinstance(payload, dict):
            raise errors.GitHubUnavailableError("GitHub returned an unexpected JSON structure.")
        return payload

    def _get(self, url: str) -> requests.Response:
        try:
            return self._session.get(
                url, headers=self._headers, timeout=self._timeout, allow_redirects=False
            )
        except requests.RequestException as exc:
            raise errors.GitHubUnavailableError(
                f"Could not reach GitHub: {type(exc).__name__}"
            ) from exc

    def _trusted_redirect_target(self, response: requests.Response) -> str:
        target = response.headers.get("Location", "")
        parts = urlsplit(target)
        if parts.scheme != "https" or parts.hostname != self._host:
            raise errors.GitHubUnavailableError("GitHub redirected to an unexpected location.")
        return target

    def _raise_for_status(self, response: requests.Response, full_name: str) -> None:
        status = response.status_code
        if status == 200:
            return
        if status in (403, 429) and self._is_rate_limited(response):
            self._raise_rate_limited(response)
        if status in (403, 404, 451):
            raise errors.RepositoryUnavailableError(
                f"GitHub answered {status} for {full_name}: it was deleted, made private or "
                "blocked. Remove it from the dashboard, or restore access."
            )
        if status == 401:
            raise errors.GitHubAuthError(
                "GitHub rejected the configured token. Check GITHUB_TOKEN."
            )
        raise errors.GitHubUnavailableError(f"GitHub answered {status} for {full_name}.")

    @staticmethod
    def _is_rate_limited(response: requests.Response) -> bool:
        return (
            response.status_code == 429
            or response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        )

    @staticmethod
    def _raise_rate_limited(response: requests.Response) -> None:
        wait = _seconds_until_retry(response)
        if wait is not None and wait > RETRYABLE_RATE_LIMIT_SECONDS:
            raise errors.RateLimitExhaustedError(
                f"GitHub's rate limit is exhausted and resets in about {wait // 60} minutes. "
                "Set GITHUB_TOKEN to raise the limit from 60 to 5000 requests per hour."
            )
        hint = f" It resets in {wait} seconds." if wait is not None else ""
        raise errors.RateLimitedError(f"GitHub's rate limit was hit.{hint}")


def _seconds_until_retry(response: requests.Response) -> int | None:
    """Seconds until GitHub accepts requests again, if the response says so."""
    try:
        if "retry-after" in response.headers:
            return max(0, int(response.headers["retry-after"]))
        if "x-ratelimit-reset" in response.headers:
            return max(0, int(response.headers["x-ratelimit-reset"]) - int(time.time()))
    except ValueError:
        pass
    return None


class InternalApiClient:
    """Calls the token-protected endpoints Django exposes for the pipeline."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
        }

    def list_active_repositories(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/internal/repositories/active/")

    def save_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Idempotent on the Django side: one snapshot per repository per UTC day."""
        return self._request("POST", "/api/internal/snapshots/", json=snapshot)

    def request_daily_report(self, snapshot_date: str, dag_run_id: str) -> dict[str, Any]:
        """Ask Django to generate the report asynchronously. Idempotent per date and run."""
        return self._request(
            "POST",
            "/api/internal/reports/",
            json={"snapshot_date": snapshot_date, "dag_run_id": dag_run_id},
        )

    def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> Any:
        try:
            response = self._session.request(
                method,
                self._base_url + path,
                headers=self._headers,
                json=json,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise errors.InternalApiUnavailableError(
                f"Could not reach the Django API: {type(exc).__name__}"
            ) from exc

        status = response.status_code
        logger.info("Django %s %s -> %s", method, path, status)
        if status in (401, 403):
            raise errors.InternalApiAuthError(
                "Django rejected the internal API token. INTERNAL_API_TOKEN must be the same "
                "for Django and Airflow."
            )
        if status >= 500 or status == 429:
            raise errors.InternalApiUnavailableError(f"Django answered {status} for {path}.")
        if status >= 300:
            raise errors.InternalApiRejectedError(
                f"Django rejected {method} {path} with {status}: {_error_summary(response)}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise errors.InternalApiUnavailableError(
                "Django returned a body that is not JSON."
            ) from exc


def _error_summary(response: requests.Response) -> str:
    """The code and message from Django's error envelope, or a short excerpt of the body."""
    try:
        error = response.json()["error"]
        summary = f"{error['code']}: {error['message']}"
        if error.get("details"):
            summary += f" {error['details']}"
        return summary
    except (ValueError, KeyError, TypeError):
        return response.text[:200]
