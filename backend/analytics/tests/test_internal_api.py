"""The endpoints Airflow calls: authentication, the work list, and idempotent snapshot ingestion."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from analytics.models import RepositorySnapshot

from .helpers import make_repository, make_snapshot

pytestmark = pytest.mark.django_db

ACTIVE_REPOSITORIES = "/api/internal/repositories/active/"
SNAPSHOTS = "/api/internal/snapshots/"


def snapshot_payload(repository, **overrides) -> dict:
    """A valid request body; any field can be overridden, including with invalid values."""
    payload = {
        "repository_id": repository.pk,
        "stars": 100,
        "forks": 20,
        "open_issues": 3,
        "collected_at": timezone.now(),
        **overrides,
    }
    if isinstance(payload["collected_at"], datetime):
        payload["collected_at"] = payload["collected_at"].isoformat()
    return payload


# --- Authentication -------------------------------------------------------------------------------
@pytest.mark.parametrize(("method", "path"), [("get", ACTIVE_REPOSITORIES), ("post", SNAPSHOTS)])
def test_internal_endpoints_require_the_token(api_client, method, path):
    response = getattr(api_client, method)(path, {}, format="json")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token",
        "Bearer",
        "Bearer test-internal-token extra",
        "Bearer TEST-INTERNAL-TOKEN",
    ],
)
def test_internal_endpoints_reject_bad_credentials(header):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=header)

    response = client.get(ACTIVE_REPOSITORIES)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


def test_other_authorization_schemes_count_as_no_credentials():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Basic dGVzdDp0ZXN0")

    assert client.get(ACTIVE_REPOSITORIES).status_code == 401


def test_public_endpoints_do_not_need_the_token(api_client):
    assert api_client.get("/api/repositories/").status_code == 200


# --- Work list ------------------------------------------------------------------------------------
def test_lists_only_active_repositories_in_a_compact_shape(internal_client):
    django = make_repository("django", "django", github_id=11)
    make_repository("gone", "away", github_id=22, is_active=False)

    response = internal_client.get(ACTIVE_REPOSITORIES)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": django.pk,
            "github_id": 11,
            "owner": "django",
            "name": "django",
            "full_name": "django/django",
        }
    ]


def test_no_active_repositories_is_an_empty_list(internal_client):
    response = internal_client.get(ACTIVE_REPOSITORIES)

    assert response.status_code == 200
    assert response.json() == []


# --- Snapshot ingestion ---------------------------------------------------------------------------
def test_stores_a_snapshot_dated_by_its_collection_time(internal_client):
    repository = make_repository()
    collected_at = datetime(2026, 3, 14, 23, 59, 30, tzinfo=UTC)

    response = internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=collected_at), format="json"
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": RepositorySnapshot.objects.get().pk,
        "repository_id": repository.pk,
        "snapshot_date": "2026-03-14",
        "outcome": "created",
    }
    snapshot = RepositorySnapshot.objects.get()
    assert (snapshot.stars, snapshot.forks, snapshot.open_issues) == (100, 20, 3)
    assert snapshot.collected_at == collected_at


def test_snapshot_date_is_the_utc_date_whatever_offset_the_caller_used(internal_client):
    repository = make_repository()
    # 01:30 on 15 March in UTC+02:00 is still 14 March in UTC.
    payload = snapshot_payload(repository, collected_at="2026-03-15T01:30:00+02:00")

    response = internal_client.post(SNAPSHOTS, payload, format="json")

    assert response.json()["snapshot_date"] == "2026-03-14"


def test_repeating_a_request_does_not_create_a_second_snapshot(internal_client):
    repository = make_repository()
    payload = snapshot_payload(repository, collected_at=datetime(2026, 3, 14, 6, 0, tzinfo=UTC))

    first = internal_client.post(SNAPSHOTS, payload, format="json")
    second = internal_client.post(SNAPSHOTS, payload, format="json")

    assert (first.status_code, first.json()["outcome"]) == (201, "created")
    assert (second.status_code, second.json()["outcome"]) == (200, "unchanged")
    assert second.json()["id"] == first.json()["id"]
    assert RepositorySnapshot.objects.count() == 1


def test_a_later_observation_on_the_same_day_replaces_the_stored_values(internal_client):
    repository = make_repository()
    morning = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)
    evening = datetime(2026, 3, 14, 18, 0, tzinfo=UTC)
    internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=morning, stars=100), format="json"
    )

    response = internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=evening, stars=105), format="json"
    )

    assert (response.status_code, response.json()["outcome"]) == (200, "updated")
    snapshot = RepositorySnapshot.objects.get()
    assert snapshot.stars == 105
    assert snapshot.collected_at == evening


def test_a_late_retry_of_an_older_observation_does_not_overwrite_newer_data(internal_client):
    repository = make_repository()
    morning = datetime(2026, 3, 14, 6, 0, tzinfo=UTC)
    evening = datetime(2026, 3, 14, 18, 0, tzinfo=UTC)
    internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=evening, stars=105), format="json"
    )

    response = internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=morning, stars=100), format="json"
    )

    assert response.json()["outcome"] == "unchanged"
    assert RepositorySnapshot.objects.get().stars == 105


def test_a_new_day_gets_its_own_snapshot(internal_client):
    repository = make_repository()
    for day in (14, 15):
        internal_client.post(
            SNAPSHOTS,
            snapshot_payload(repository, collected_at=datetime(2026, 3, day, 6, 0, tzinfo=UTC)),
            format="json",
        )

    dates = RepositorySnapshot.objects.values_list("snapshot_date", flat=True)
    assert [d.isoformat() for d in dates] == ["2026-03-14", "2026-03-15"]


def test_database_refuses_a_second_snapshot_for_the_same_repository_and_day():
    """The uniqueness guarantee holds even for code that bypasses the service."""
    repository = make_repository()
    day = timezone.now().date()
    make_snapshot(repository, day)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_snapshot(repository, day)


def test_accepts_a_snapshot_for_a_repository_removed_mid_run(internal_client):
    repository = make_repository(is_active=False, deactivated_at=timezone.now())

    response = internal_client.post(SNAPSHOTS, snapshot_payload(repository), format="json")

    assert response.status_code == 201


def test_unknown_repository_is_not_found(internal_client):
    payload = {
        "repository_id": 999,
        "stars": 1,
        "forks": 1,
        "open_issues": 1,
        "collected_at": timezone.now().isoformat(),
    }

    response = internal_client.post(SNAPSHOTS, payload, format="json")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stars", -1),
        ("forks", -5),
        ("open_issues", "many"),
        ("stars", 2**31),
        ("stars", None),
        ("repository_id", 0),
        ("collected_at", "yesterday"),
        ("collected_at", None),
    ],
)
def test_rejects_invalid_metrics(internal_client, field, value):
    repository = make_repository()

    response = internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, **{field: value}), format="json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert field in response.json()["error"]["details"]["fields"]
    assert not RepositorySnapshot.objects.exists()


def test_rejects_a_collection_time_in_the_future(internal_client):
    repository = make_repository()
    tomorrow = timezone.now() + timedelta(days=1)

    response = internal_client.post(
        SNAPSHOTS, snapshot_payload(repository, collected_at=tomorrow), format="json"
    )

    assert response.status_code == 400
    assert "collected_at" in response.json()["error"]["details"]["fields"]


@pytest.mark.parametrize("missing", ["repository_id", "stars", "forks", "open_issues"])
def test_rejects_missing_fields(internal_client, missing):
    payload = snapshot_payload(make_repository())
    del payload[missing]

    response = internal_client.post(SNAPSHOTS, payload, format="json")

    assert response.status_code == 400
    assert missing in response.json()["error"]["details"]["fields"]
