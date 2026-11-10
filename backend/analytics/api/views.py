"""Public endpoints used by the dashboard.

There is no user authentication: this is a local demonstration project, and anyone who can reach
the API can manage the repository list. See docs/ARCHITECTURE.md for the security limits.
"""

from __future__ import annotations

from django.db import connection
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from analytics.application import queries
from analytics.application.repositories import add_repository, deactivate_repository
from analytics.infrastructure.github_client import build_github_gateway

from .serializers import (
    DailyReportSerializer,
    DashboardSummarySerializer,
    PipelineStatusSerializer,
    RepositoryCreateSerializer,
    RepositorySerializer,
    SnapshotSerializer,
)

DEFAULT_SNAPSHOT_DAYS = 90
MAX_SNAPSHOT_DAYS = 366
DEFAULT_REPORT_LIMIT = 30
MAX_REPORT_LIMIT = 100


def bounded_int_param(request, name: str, *, default: int, maximum: int) -> int:
    """Read a positive integer query parameter, rejecting anything outside 1..maximum."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise ValidationError({name: [f"Must be a whole number between 1 and {maximum}."]})
    return value


class HealthView(APIView):
    """Liveness and database connectivity, for container health checks."""

    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})


class RepositoryListCreateView(APIView):
    throttle_scope = "repository-create"

    def get_throttles(self):
        # Only creation is throttled: each call triggers one outbound GitHub request, so an
        # unauthenticated client must not be able to burn the GitHub rate limit at will.
        return [ScopedRateThrottle()] if self.request.method == "POST" else []

    def get(self, request):
        status_filter = request.query_params.get("status", "active")
        if status_filter not in queries.REPOSITORY_STATUS_FILTERS:
            allowed = ", ".join(queries.REPOSITORY_STATUS_FILTERS)
            raise ValidationError({"status": [f"Must be one of: {allowed}."]})
        repositories = queries.list_repositories(status_filter)
        return Response(RepositorySerializer(repositories, many=True).data)

    def post(self, request):
        serializer = RepositoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = add_repository(serializer.validated_data["url"], github=build_github_gateway())

        repository = queries.get_repository(result.repository.pk)
        body = {**RepositorySerializer(repository).data, "reactivated": result.reactivated}
        # 201 for a new resource, 200 when an existing one was brought back.
        http_status = status.HTTP_200_OK if result.reactivated else status.HTTP_201_CREATED
        return Response(body, status=http_status)


class RepositoryDetailView(APIView):
    def delete(self, request, pk: int):
        """Soft delete: the repository leaves active tracking, its snapshots stay."""
        deactivate_repository(pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RepositorySnapshotsView(APIView):
    def get(self, request, pk: int):
        days = bounded_int_param(
            request, "days", default=DEFAULT_SNAPSHOT_DAYS, maximum=MAX_SNAPSHOT_DAYS
        )
        snapshots = queries.list_snapshots(pk, days=days)
        return Response(SnapshotSerializer(snapshots, many=True).data)


class DashboardSummaryView(APIView):
    def get(self, request):
        return Response(DashboardSummarySerializer(queries.dashboard_summary()).data)


class ReportListView(APIView):
    def get(self, request):
        limit = bounded_int_param(
            request, "limit", default=DEFAULT_REPORT_LIMIT, maximum=MAX_REPORT_LIMIT
        )
        return Response(DailyReportSerializer(queries.list_reports(limit=limit), many=True).data)


class PipelineStatusView(APIView):
    def get(self, request):
        return Response(PipelineStatusSerializer(queries.pipeline_status()).data)
