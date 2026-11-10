"""Cross-cutting HTTP behaviour: health, CORS and the shape of error responses."""

import pytest

pytestmark = pytest.mark.django_db

ALLOWED_ORIGIN = "http://localhost:5173"


def test_health_checks_the_database(api_client):
    response = api_client.get("/api/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_configured_origins_only(api_client, settings):
    settings.CORS_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]

    allowed = api_client.get("/api/repositories/", HTTP_ORIGIN=ALLOWED_ORIGIN)
    refused = api_client.get("/api/repositories/", HTTP_ORIGIN="https://evil.example")

    assert allowed.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "Access-Control-Allow-Origin" not in refused.headers


def test_cors_preflight_for_the_dashboard_methods(api_client, settings):
    settings.CORS_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]

    response = api_client.options(
        "/api/repositories/1/",
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="DELETE",
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "DELETE" in response.headers["Access-Control-Allow-Methods"]


def test_cors_never_allows_credentials(api_client, settings):
    settings.CORS_ALLOWED_ORIGINS = [ALLOWED_ORIGIN]

    response = api_client.get("/api/repositories/", HTTP_ORIGIN=ALLOWED_ORIGIN)

    assert "Access-Control-Allow-Credentials" not in response.headers


def test_unknown_paths_answer_with_the_json_error_envelope(api_client):
    response = api_client.get("/api/does-not-exist/")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "This endpoint does not exist.", "details": {}}
    }


def test_unsupported_methods_answer_with_the_json_error_envelope(api_client):
    response = api_client.put("/api/repositories/", {}, format="json")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_malformed_json_is_a_parse_error(api_client):
    response = api_client.post(
        "/api/repositories/", data="{not json", content_type="application/json"
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "parse_error"


def test_only_json_request_bodies_are_accepted(api_client):
    response = api_client.post(
        "/api/repositories/", data="url=x", content_type="application/x-www-form-urlencoded"
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
