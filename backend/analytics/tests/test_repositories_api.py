"""Repository management through the public REST API, with GitHub mocked at the HTTP level."""

from datetime import timedelta

import pytest
import requests
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle

from analytics.models import Repository, RepositorySnapshot

from .helpers import github_payload, github_url, make_repository, make_snapshot

pytestmark = pytest.mark.django_db

ENDPOINT = "/api/repositories/"
DJANGO_URL = "https://github.com/django/django"


def add(api_client, url=DJANGO_URL):
    return api_client.post(ENDPOINT, {"url": url}, format="json")


def error_code(response) -> str:
    return response.json()["error"]["code"]


# --- Creating -------------------------------------------------------------------------------------
def test_adds_a_valid_public_repository(api_client, github):
    github.get(github_url("django", "django"), json=github_payload())

    response = add(api_client)

    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == "django/django"
    assert body["html_url"] == "https://github.com/django/django"
    assert body["github_id"] == 4164482
    assert body["is_active"] is True
    assert body["reactivated"] is False
    assert body["latest_snapshot"] is None
    assert body["last_synced_at"] is None
    assert Repository.objects.filter(owner="django", name="django", is_active=True).exists()


def test_stores_the_canonical_name_reported_by_github(api_client, github):
    """The user's casing is not trusted; GitHub's is."""
    github.get(github_url("Django", "DJANGO"), json=github_payload())

    response = add(api_client, "https://github.com/Django/DJANGO.git")

    assert response.status_code == 201
    assert response.json()["full_name"] == "django/django"


def test_builds_the_github_request_from_validated_identifiers_only(api_client, github):
    github.get(github_url("django", "django"), json=github_payload())

    add(api_client, "https://www.github.com/django/django/")

    assert [call.request.url for call in github.calls] == [
        "https://api.github.com/repos/django/django"
    ]


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "http://github.com/django/django",
        "https://gitlab.com/django/django",
        "https://github.com.evil.example/django/django",
        "https://github.com/django/django/issues",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_rejects_invalid_urls_without_calling_github(api_client, github, url):
    response = add(api_client, url)

    assert response.status_code == 400
    assert error_code(response) == "invalid_repository_url"
    assert len(github.calls) == 0
    assert not Repository.objects.exists()


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": None}, {"link": DJANGO_URL}])
def test_rejects_requests_without_a_url(api_client, github, payload):
    response = api_client.post(ENDPOINT, payload, format="json")

    assert response.status_code == 400
    assert error_code(response) == "validation_error"
    assert "url" in response.json()["error"]["details"]["fields"]


def test_missing_or_private_repository_is_reported_as_unavailable(api_client, github):
    """GitHub answers 404 for both cases, so the API must not claim that nothing exists there."""
    github.get(github_url("django", "django"), status=404, json={"message": "Not Found"})

    response = add(api_client)

    assert response.status_code == 422
    assert error_code(response) == "repository_unavailable"
    message = response.json()["error"]["message"].lower()
    assert "may not exist" in message
    assert "private" in message
    assert not Repository.objects.exists()


@pytest.mark.parametrize(("private", "visibility"), [(True, "private"), (False, "internal")])
def test_repository_visible_to_the_token_but_not_public_is_refused(
    api_client, github, private, visibility
):
    github.get(
        github_url("django", "django"),
        json=github_payload(private=private, visibility=visibility),
    )

    response = add(api_client)

    assert response.status_code == 422
    assert error_code(response) == "repository_not_public"
    assert not Repository.objects.exists()


def test_github_access_denied(api_client, github):
    github.get(github_url("django", "django"), status=403, json={"message": "blocked"})

    response = add(api_client)

    assert response.status_code == 422
    assert error_code(response) == "repository_access_denied"


# --- Temporary GitHub problems --------------------------------------------------------------------
def test_github_rate_limit_becomes_a_temporary_error_with_retry_after(api_client, github):
    github.get(
        github_url("django", "django"),
        status=403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "retry-after": "120"},
    )

    response = add(api_client)

    assert response.status_code == 503
    assert error_code(response) == "github_rate_limited"
    assert response.headers["Retry-After"] == "120"
    assert response.json()["error"]["details"] == {"retry_after": 120}
    assert not Repository.objects.exists()


def test_github_timeout(api_client, github):
    github.get(github_url("django", "django"), body=requests.exceptions.ConnectTimeout())

    response = add(api_client)

    assert response.status_code == 504
    assert error_code(response) == "github_timeout"
    assert not Repository.objects.exists()


@pytest.mark.parametrize("status", [500, 503])
def test_github_service_error(api_client, github, status):
    github.get(github_url("django", "django"), status=status)

    response = add(api_client)

    assert response.status_code == 502
    assert error_code(response) == "github_unavailable"
    assert not Repository.objects.exists()


# --- Duplicates -----------------------------------------------------------------------------------
def test_duplicate_is_a_conflict_and_does_not_call_github(api_client, github):
    make_repository("django", "django")

    response = add(api_client, "https://github.com/DJANGO/Django")

    assert response.status_code == 409
    assert error_code(response) == "duplicate_repository"
    assert len(github.calls) == 0
    assert Repository.objects.count() == 1


def test_renamed_repository_is_recognised_by_its_github_id(api_client, github):
    """Tracked as old/name; the user now pastes the new address of the same repository."""
    make_repository("old-owner", "project", github_id=77)
    github.get(
        github_url("new-owner", "project"),
        json=github_payload(github_id=77, owner="new-owner", name="project"),
    )

    response = add(api_client, "https://github.com/new-owner/project")

    assert response.status_code == 409
    assert Repository.objects.count() == 1


def test_database_refuses_case_insensitive_duplicates():
    """The constraint, not just the service, guards against concurrent duplicate inserts."""
    make_repository("django", "django", github_id=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_repository("Django", "DJANGO", github_id=2)


def test_database_refuses_duplicate_github_ids():
    make_repository("django", "django", github_id=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_repository("someone", "else", github_id=1)


# --- Soft delete and reactivation -----------------------------------------------------------------
def test_removing_keeps_the_row_and_its_history(api_client):
    repository = make_repository()
    make_snapshot(repository, timezone.now().date())

    response = api_client.delete(f"{ENDPOINT}{repository.pk}/")

    assert response.status_code == 204
    repository.refresh_from_db()
    assert repository.is_active is False
    assert repository.deactivated_at is not None
    assert RepositorySnapshot.objects.filter(repository=repository).count() == 1


def test_removing_twice_is_harmless(api_client):
    repository = make_repository(is_active=False, deactivated_at=timezone.now())

    assert api_client.delete(f"{ENDPOINT}{repository.pk}/").status_code == 204


def test_removing_an_unknown_repository_is_not_found(api_client):
    response = api_client.delete(f"{ENDPOINT}999/")

    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_adding_a_removed_repository_reactivates_it(api_client, github):
    repository = make_repository(is_active=False, deactivated_at=timezone.now())
    make_snapshot(repository, timezone.now().date() - timedelta(days=1))
    github.get(github_url("django", "django"), json=github_payload())

    response = add(api_client)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == repository.pk
    assert body["reactivated"] is True
    assert body["is_active"] is True
    assert body["deactivated_at"] is None
    assert body["latest_snapshot"] is not None  # history survived
    assert Repository.objects.count() == 1


def test_reactivation_is_refused_when_github_no_longer_shows_the_repository(api_client, github):
    repository = make_repository(is_active=False, deactivated_at=timezone.now())
    github.get(github_url("django", "django"), status=404, json={"message": "Not Found"})

    response = add(api_client)

    assert response.status_code == 422
    repository.refresh_from_db()
    assert repository.is_active is False


# --- Listing --------------------------------------------------------------------------------------
def test_lists_active_repositories_with_their_latest_metrics(api_client):
    today = timezone.now().date()
    django = make_repository("django", "django", github_id=1)
    make_snapshot(django, today - timedelta(days=1), stars=10)
    latest = make_snapshot(django, today, stars=12, forks=3, open_issues=4)
    make_repository("apache", "airflow", github_id=2)
    make_repository("gone", "away", github_id=3, is_active=False)

    response = api_client.get(ENDPOINT)

    assert response.status_code == 200
    by_name = {item["full_name"]: item for item in response.json()}
    assert set(by_name) == {"django/django", "apache/airflow"}
    assert by_name["django/django"]["latest_snapshot"] == {
        "snapshot_date": today.isoformat(),
        "stars": 12,
        "forks": 3,
        "open_issues": 4,
        "collected_at": latest.collected_at.isoformat().replace("+00:00", "Z"),
    }
    assert by_name["django/django"]["last_synced_at"] is not None
    assert by_name["apache/airflow"]["latest_snapshot"] is None
    assert by_name["apache/airflow"]["last_synced_at"] is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", {"django/django"}),
        ("inactive", {"gone/away"}),
        ("all", {"django/django", "gone/away"}),
    ],
)
def test_filters_by_status(api_client, status, expected):
    make_repository("django", "django", github_id=1)
    make_repository("gone", "away", github_id=2, is_active=False)

    response = api_client.get(ENDPOINT, {"status": status})

    assert {item["full_name"] for item in response.json()} == expected


def test_rejects_an_unknown_status_filter(api_client):
    response = api_client.get(ENDPOINT, {"status": "archived"})

    assert response.status_code == 400
    assert error_code(response) == "validation_error"


# --- Abuse protection -----------------------------------------------------------------------------
def test_creation_is_throttled(api_client, github, monkeypatch):
    """Each POST triggers an outbound GitHub call, so the endpoint is rate limited."""
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", {"repository-create": "2/min"})

    statuses = [add(api_client, "nonsense").status_code for _ in range(3)]

    assert statuses == [400, 400, 429]
    assert error_code(add(api_client, "nonsense")) == "throttled"
    # Reading is never throttled.
    assert api_client.get(ENDPOINT).status_code == 200
