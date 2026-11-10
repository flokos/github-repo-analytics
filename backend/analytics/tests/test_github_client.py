"""The GitHub adapter against mocked HTTP: every response GitHub can give maps to one outcome."""

import logging
import time

import pytest
import requests

from analytics.domain import errors
from analytics.domain.repository_url import RepositoryRef
from analytics.infrastructure.github_client import GitHubApiClient

from .helpers import GITHUB_API, github_payload, github_url

REF = RepositoryRef(owner="django", name="django")
URL = github_url("django", "django")


def make_client(token: str = "") -> GitHubApiClient:
    return GitHubApiClient(
        base_url=GITHUB_API, api_version="2026-03-10", token=token, timeout=(1, 1)
    )


def test_returns_metadata_for_a_public_repository(github):
    github.get(URL, json=github_payload())

    metadata = make_client().get_repository(REF)

    assert metadata.github_id == 4164482
    assert (metadata.owner, metadata.name) == ("django", "django")
    assert metadata.is_public


def test_sends_versioned_headers_and_no_credentials_without_a_token(github):
    github.get(URL, json=github_payload())

    make_client().get_repository(REF)

    headers = github.calls[0].request.headers
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2026-03-10"
    assert headers["User-Agent"] == "github-repo-analytics"
    assert "Authorization" not in headers


def test_sends_the_configured_token_and_never_logs_it(github, caplog):
    github.get(URL, json=github_payload())

    with caplog.at_level(logging.DEBUG):
        make_client(token="ghp_secret_value").get_repository(REF)

    assert github.calls[0].request.headers["Authorization"] == "Bearer ghp_secret_value"
    assert "ghp_secret_value" not in caplog.text


@pytest.mark.parametrize(
    ("private", "visibility"),
    [(True, "private"), (False, "internal"), (True, "public"), (False, "")],
)
def test_reports_anything_but_public_as_not_public(github, private, visibility):
    github.get(URL, json=github_payload(private=private, visibility=visibility))

    assert not make_client().get_repository(REF).is_public


def test_missing_visibility_is_treated_as_not_public(github):
    payload = github_payload()
    del payload["visibility"]
    github.get(URL, json=payload)

    assert not make_client().get_repository(REF).is_public


def test_404_is_unavailable(github):
    github.get(URL, status=404, json={"message": "Not Found"})

    with pytest.raises(errors.RepositoryUnavailable):
        make_client().get_repository(REF)


def test_exhausted_primary_rate_limit_reports_when_to_retry(github):
    reset_at = int(time.time()) + 600
    github.get(
        URL,
        status=403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)},
    )

    with pytest.raises(errors.GitHubRateLimited) as excinfo:
        make_client().get_repository(REF)

    assert 590 <= excinfo.value.retry_after <= 600


def test_secondary_rate_limit_uses_retry_after_header(github):
    github.get(URL, status=429, json={"message": "slow down"}, headers={"retry-after": "30"})

    with pytest.raises(errors.GitHubRateLimited) as excinfo:
        make_client().get_repository(REF)

    assert excinfo.value.retry_after == 30


def test_rate_limit_without_timing_headers_has_no_retry_hint(github):
    github.get(URL, status=429, json={"message": "slow down"})

    with pytest.raises(errors.GitHubRateLimited) as excinfo:
        make_client().get_repository(REF)

    assert excinfo.value.retry_after is None


@pytest.mark.parametrize("status", [403, 451])
def test_refusal_without_rate_limit_headers_is_access_denied(github, status):
    github.get(
        URL,
        status=status,
        json={"message": "Repository access blocked"},
        headers={"x-ratelimit-remaining": "42"},
    )

    with pytest.raises(errors.RepositoryAccessDenied):
        make_client().get_repository(REF)


@pytest.mark.parametrize("status", [401, 500, 502, 503])
def test_other_failures_are_reported_as_github_unavailable(github, status):
    github.get(URL, status=status, json={"message": "nope"})

    with pytest.raises(errors.GitHubUnavailable):
        make_client().get_repository(REF)


def test_timeout(github):
    github.get(URL, body=requests.exceptions.ReadTimeout())

    with pytest.raises(errors.GitHubTimeout):
        make_client().get_repository(REF)


def test_connection_failure(github):
    github.get(URL, body=requests.exceptions.ConnectionError())

    with pytest.raises(errors.GitHubUnavailable):
        make_client().get_repository(REF)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        [],
        {},
        {**github_payload(), "id": "4164482"},
        {**github_payload(), "id": True},
        {**github_payload(), "owner": None},
        {**github_payload(), "owner": {"login": "../etc"}},
        {**github_payload(), "name": "bad name"},
        {**github_payload(), "private": "false"},
    ],
)
def test_unexpected_payloads_are_rejected(github, payload):
    if isinstance(payload, str):
        github.get(URL, body=payload)
    else:
        github.get(URL, json=payload)

    with pytest.raises(errors.GitHubUnavailable):
        make_client().get_repository(REF)


def test_follows_one_redirect_within_the_github_api(github):
    """GitHub answers 301 when a repository was renamed or transferred."""
    moved_to = f"{GITHUB_API}/repositories/4164482"
    github.get(URL, status=301, headers={"Location": moved_to})
    github.get(moved_to, json=github_payload(owner="django-new", name="django"))

    metadata = make_client().get_repository(REF)

    assert metadata.owner == "django-new"
    assert [call.request.url for call in github.calls] == [URL, moved_to]


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/repositories/1",
        "http://api.github.com/repositories/1",
        "https://api.github.com.evil.example/repositories/1",
        "",
    ],
)
def test_refuses_redirects_that_leave_the_github_api(github, location):
    github.get(URL, status=301, headers={"Location": location})

    with pytest.raises(errors.GitHubUnavailable):
        make_client().get_repository(REF)

    assert len(github.calls) == 1


def test_does_not_follow_a_second_redirect(github):
    first = f"{GITHUB_API}/repositories/1"
    github.get(URL, status=301, headers={"Location": first})
    github.get(first, status=301, headers={"Location": f"{GITHUB_API}/repositories/2"})

    with pytest.raises(errors.GitHubUnavailable):
        make_client().get_repository(REF)

    assert len(github.calls) == 2
