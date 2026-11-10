"""Both HTTP clients against mocked responses: every outcome maps to one typed error."""

import json
import logging
import time

import pytest
import requests
from helpers import (
    ACTIVE_REPOSITORIES_URL,
    DJANGO,
    DJANGO_API,
    GITHUB_API,
    REPORTS_URL,
    SNAPSHOTS_URL,
    github_payload,
    github_url,
)

from github_analytics import errors
from github_analytics.clients import GitHubClient, InternalApiClient

URL = github_url(DJANGO)


def github_client(token: str = "") -> GitHubClient:
    return GitHubClient(GITHUB_API, token=token, timeout=(1, 1))


def internal_client(token: str = "internal-secret") -> InternalApiClient:
    return InternalApiClient(DJANGO_API, token=token, timeout=(1, 1))


# --- GitHub ---------------------------------------------------------------------------------------
def test_fetches_a_repository(http):
    http.get(URL, json=github_payload(DJANGO))

    payload = github_client().fetch_repository("django", "django")

    assert payload["stargazers_count"] == 1200
    headers = http.calls[0].request.headers
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert "Authorization" not in headers


def test_sends_the_token_and_never_logs_it(http, caplog):
    http.get(URL, json=github_payload(DJANGO))

    with caplog.at_level(logging.DEBUG):
        github_client(token="ghp_secret_value").fetch_repository("django", "django")

    assert http.calls[0].request.headers["Authorization"] == "Bearer ghp_secret_value"
    assert "ghp_secret_value" not in caplog.text


@pytest.mark.parametrize("status", [403, 404, 451])
def test_repository_gone_or_blocked_is_permanent(http, status):
    http.get(URL, status=status, json={"message": "nope"})

    with pytest.raises(errors.RepositoryUnavailableError, match="django/django"):
        github_client().fetch_repository("django", "django")


def test_rejected_token_is_permanent(http):
    http.get(URL, status=401, json={"message": "Bad credentials"})

    with pytest.raises(errors.GitHubAuthError):
        github_client(token="expired").fetch_repository("django", "django")


def test_rate_limit_that_resets_soon_is_worth_retrying(http):
    http.get(URL, status=429, json={"message": "slow down"}, headers={"retry-after": "20"})

    with pytest.raises(errors.RateLimitedError, match="20 seconds"):
        github_client().fetch_repository("django", "django")


def test_rate_limit_that_resets_far_away_fails_without_burning_retries(http):
    reset_at = int(time.time()) + 3000
    http.get(
        URL,
        status=403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)},
    )

    with pytest.raises(errors.RateLimitExhaustedError, match="GITHUB_TOKEN"):
        github_client().fetch_repository("django", "django")


def test_rate_limit_without_timing_information_is_retried(http):
    http.get(URL, status=429, json={"message": "slow down"})

    with pytest.raises(errors.RateLimitedError):
        github_client().fetch_repository("django", "django")


@pytest.mark.parametrize("status", [500, 502, 503])
def test_server_errors_are_transient(http, status):
    http.get(URL, status=status)

    with pytest.raises(errors.GitHubUnavailableError):
        github_client().fetch_repository("django", "django")


@pytest.mark.parametrize(
    "failure", [requests.exceptions.ConnectTimeout(), requests.exceptions.ConnectionError()]
)
def test_network_failures_are_transient(http, failure):
    http.get(URL, body=failure)

    with pytest.raises(errors.GitHubUnavailableError):
        github_client().fetch_repository("django", "django")


@pytest.mark.parametrize("body", ["<html>oops</html>", "[]", '"text"'])
def test_unreadable_bodies_are_transient(http, body):
    http.get(URL, body=body)

    with pytest.raises(errors.GitHubUnavailableError):
        github_client().fetch_repository("django", "django")


def test_follows_one_rename_redirect_on_the_github_api(http):
    moved_to = f"{GITHUB_API}/repositories/4164482"
    http.get(URL, status=301, headers={"Location": moved_to})
    http.get(moved_to, json=github_payload(DJANGO))

    assert github_client().fetch_repository("django", "django")["id"] == 4164482
    assert [call.request.url for call in http.calls] == [URL, moved_to]


@pytest.mark.parametrize(
    "location", ["https://evil.example/repositories/1", "http://api.github.com/repositories/1", ""]
)
def test_refuses_redirects_that_leave_the_github_api(http, location):
    http.get(URL, status=301, headers={"Location": location})

    with pytest.raises(errors.GitHubUnavailableError):
        github_client(token="ghp_secret_value").fetch_repository("django", "django")

    assert len(http.calls) == 1


# --- Django internal API --------------------------------------------------------------------------
def test_lists_active_repositories_with_the_internal_token(http):
    http.get(ACTIVE_REPOSITORIES_URL, json=[DJANGO])

    assert internal_client().list_active_repositories() == [DJANGO]
    assert http.calls[0].request.headers["Authorization"] == "Bearer internal-secret"


def test_saves_a_snapshot(http):
    stored = {"id": 9, "repository_id": 1, "snapshot_date": "2026-03-14", "outcome": "created"}
    http.post(SNAPSHOTS_URL, status=201, json=stored)
    snapshot = {
        "repository_id": 1,
        "stars": 1,
        "forks": 2,
        "open_issues": 3,
        "collected_at": "2026-03-14T06:00:00+00:00",
    }

    assert internal_client().save_snapshot(snapshot) == stored
    assert json.loads(http.calls[0].request.body) == snapshot


def test_requests_a_daily_report(http):
    accepted = {"id": 4, "snapshot_date": "2026-03-14", "status": "queued"}
    http.post(REPORTS_URL, status=202, json=accepted)

    assert internal_client().request_daily_report("2026-03-14", "scheduled__2026-03-14") == accepted
    assert json.loads(http.calls[0].request.body) == {
        "snapshot_date": "2026-03-14",
        "dag_run_id": "scheduled__2026-03-14",
    }


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_internal_token_is_permanent(http, status):
    http.get(ACTIVE_REPOSITORIES_URL, status=status, json={"error": {"code": "x", "message": "y"}})

    with pytest.raises(errors.InternalApiAuthError, match="INTERNAL_API_TOKEN"):
        internal_client().list_active_repositories()


def test_invalid_request_is_permanent_and_explains_why(http):
    http.post(
        SNAPSHOTS_URL,
        status=400,
        json={
            "error": {
                "code": "validation_error",
                "message": "The request contains invalid data.",
                "details": {
                    "fields": {"stars": ["Ensure this value is greater than or equal to 0."]}
                },
            }
        },
    )

    with pytest.raises(errors.InternalApiRejectedError) as excinfo:
        internal_client().save_snapshot({"stars": -1})

    assert "validation_error" in str(excinfo.value)
    assert "stars" in str(excinfo.value)


def test_rejection_with_an_unexpected_body_still_produces_a_message(http):
    http.post(SNAPSHOTS_URL, status=404, body="<html>Not Found</html>")

    with pytest.raises(errors.InternalApiRejectedError, match="404"):
        internal_client().save_snapshot({})


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_django_trouble_is_transient(http, status):
    http.post(REPORTS_URL, status=status)

    with pytest.raises(errors.InternalApiUnavailableError):
        internal_client().request_daily_report("2026-03-14", "run")


@pytest.mark.parametrize(
    "failure", [requests.exceptions.ReadTimeout(), requests.exceptions.ConnectionError()]
)
def test_django_being_unreachable_is_transient(http, failure):
    http.get(ACTIVE_REPOSITORIES_URL, body=failure)

    with pytest.raises(errors.InternalApiUnavailableError):
        internal_client().list_active_repositories()


def test_internal_token_is_never_logged(http, caplog):
    http.get(ACTIVE_REPOSITORIES_URL, json=[])

    with caplog.at_level(logging.DEBUG):
        internal_client(token="very-secret-internal-token").list_active_repositories()

    assert "very-secret-internal-token" not in caplog.text
