"""Turns every failure into the same JSON envelope.

    {"error": {"code": "duplicate_repository", "message": "...", "details": {...}}}

`code` is stable and machine-readable (the dashboard switches on it); `message` is for people.
This module is the single place that decides which HTTP status an error becomes.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import JsonResponse
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from analytics.domain import errors

logger = logging.getLogger(__name__)

# Domain error -> (HTTP status, error code).
_DOMAIN_ERRORS: dict[type[errors.AnalyticsError], tuple[int, str]] = {
    errors.InvalidRepositoryUrl: (status.HTTP_400_BAD_REQUEST, "invalid_repository_url"),
    errors.DuplicateRepository: (status.HTTP_409_CONFLICT, "duplicate_repository"),
    errors.RepositoryNotFound: (status.HTTP_404_NOT_FOUND, "not_found"),
    # The submitted URL is well formed, but the repository behind it cannot be tracked.
    errors.RepositoryUnavailable: (status.HTTP_422_UNPROCESSABLE_ENTITY, "repository_unavailable"),
    errors.RepositoryNotPublic: (status.HTTP_422_UNPROCESSABLE_ENTITY, "repository_not_public"),
    errors.RepositoryAccessDenied: (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "repository_access_denied",
    ),
    # Temporary upstream conditions: the same request may succeed later.
    errors.GitHubRateLimited: (status.HTTP_503_SERVICE_UNAVAILABLE, "github_rate_limited"),
    errors.GitHubTimeout: (status.HTTP_504_GATEWAY_TIMEOUT, "github_timeout"),
    errors.GitHubUnavailable: (status.HTTP_502_BAD_GATEWAY, "github_unavailable"),
}

_DRF_CODES: dict[type[exceptions.APIException], str] = {
    exceptions.ValidationError: "validation_error",
    exceptions.ParseError: "parse_error",
    exceptions.NotAuthenticated: "not_authenticated",
    exceptions.AuthenticationFailed: "authentication_failed",
    exceptions.PermissionDenied: "permission_denied",
    exceptions.NotFound: "not_found",
    exceptions.MethodNotAllowed: "method_not_allowed",
    exceptions.UnsupportedMediaType: "unsupported_media_type",
    exceptions.Throttled: "throttled",
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    if isinstance(exc, errors.AnalyticsError):
        return _domain_error_response(exc)

    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled error in %s", context.get("view"), exc_info=exc)
        return Response(
            error_body("internal_error", "An unexpected error occurred."),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code = next(
        (code for exc_type, code in _DRF_CODES.items() if isinstance(exc, exc_type)), "error"
    )
    if isinstance(exc, exceptions.ValidationError):
        message, details = "The request contains invalid data.", {"fields": response.data}
    elif isinstance(exc, exceptions.Throttled):
        message = "Too many requests. Slow down and try again shortly."
        details = {"retry_after": exc.wait}
    else:
        message, details = str(getattr(exc, "detail", "Request failed.")), {}
    response.data = error_body(code, message, details)
    return response


def _domain_error_response(exc: errors.AnalyticsError) -> Response:
    http_status, code = next(
        mapping for exc_type, mapping in _DOMAIN_ERRORS.items() if isinstance(exc, exc_type)
    )
    details: dict[str, Any] = {}
    headers: dict[str, str] = {}
    if isinstance(exc, errors.GitHubRateLimited) and exc.retry_after is not None:
        details["retry_after"] = exc.retry_after
        headers["Retry-After"] = str(exc.retry_after)
    if http_status >= 500:
        logger.warning("Upstream failure answered with %s: %s", http_status, code)
    return Response(error_body(code, exc.message, details), status=http_status, headers=headers)


def not_found(request, exception=None) -> JsonResponse:
    """Django-level 404 (no URL pattern matched)."""
    return JsonResponse(error_body("not_found", "This endpoint does not exist."), status=404)


def server_error(request) -> JsonResponse:
    """Django-level 500 (an error outside a DRF view)."""
    return JsonResponse(error_body("internal_error", "An unexpected error occurred."), status=500)
