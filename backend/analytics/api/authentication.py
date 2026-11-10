"""Service-to-service authentication for the internal endpoints Airflow calls."""

from __future__ import annotations

import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission


class InternalService:
    """The authenticated principal for a request that presented the internal token."""

    is_authenticated = True

    def __str__(self) -> str:
        return "internal-service"


class InternalTokenAuthentication(BaseAuthentication):
    """`Authorization: Bearer <INTERNAL_API_TOKEN>`.

    A single shared secret is enough for one trusted caller on a private network. It is compared
    in constant time, and it is never logged.
    """

    keyword = b"bearer"

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None  # no credentials: the permission class answers 401
        if len(parts) != 2:
            raise AuthenticationFailed("Malformed Authorization header.")

        expected = settings.INTERNAL_API_TOKEN.encode()
        if not hmac.compare_digest(parts[1], expected):
            raise AuthenticationFailed("Invalid internal API token.")
        return InternalService(), None

    def authenticate_header(self, request) -> str:
        # Makes DRF answer 401 with a WWW-Authenticate header instead of 403.
        return "Bearer"


class IsInternalService(BasePermission):
    message = "This endpoint is reserved for the pipeline."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, InternalService)
