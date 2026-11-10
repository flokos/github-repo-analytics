import pytest
import responses
from django.core.cache import cache
from rest_framework.test import APIClient

from .helpers import INTERNAL_TOKEN


@pytest.fixture(autouse=True)
def _isolated_settings(settings):
    """Known tokens for every test, and a clean cache (throttle counters live there)."""
    settings.INTERNAL_API_TOKEN = INTERNAL_TOKEN
    settings.GITHUB_TOKEN = ""
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def internal_client() -> APIClient:
    """A client that authenticates the way Airflow does."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {INTERNAL_TOKEN}")
    return client


@pytest.fixture
def github():
    """Intercepts all outbound HTTP. A request to an unregistered URL fails the test, so no test
    can reach the real GitHub by accident."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock
