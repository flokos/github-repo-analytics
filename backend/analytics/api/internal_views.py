"""Internal endpoints called by the Airflow pipeline.

Every view here requires the internal bearer token. In the Compose stack nginx additionally
refuses to route `/api/internal/`, so these endpoints cannot be reached through the dashboard's
origin.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.application import queries
from analytics.application.reports import request_daily_report
from analytics.application.snapshots import SnapshotInput, SnapshotOutcome, record_snapshot
from analytics.tasks import dispatch_report_generation

from .authentication import InternalTokenAuthentication, IsInternalService
from .serializers import (
    ActiveRepositorySerializer,
    ReportRequestResultSerializer,
    ReportRequestSerializer,
    SnapshotWriteResultSerializer,
    SnapshotWriteSerializer,
)


class InternalAPIView(APIView):
    authentication_classes = [InternalTokenAuthentication]
    permission_classes = [IsInternalService]


class ActiveRepositoriesView(InternalAPIView):
    """Task 1 of the DAG: the repositories to collect in this run."""

    def get(self, request):
        repositories = queries.list_active_repositories()
        return Response(ActiveRepositorySerializer(repositories, many=True).data)


class SnapshotIngestView(InternalAPIView):
    """Task 3 of the DAG: store one repository's metrics. Idempotent per repository and day."""

    def post(self, request):
        serializer = SnapshotWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = record_snapshot(SnapshotInput(**serializer.validated_data))

        created = result.outcome is SnapshotOutcome.CREATED
        return Response(
            SnapshotWriteResultSerializer(result).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ReportRequestView(InternalAPIView):
    """Task 4 of the DAG: ask for the daily report of one snapshot date.

    Answers 202 Accepted: the report is only queued here. A Django Celery worker generates it,
    and its progress is visible at `/api/reports/`. Repeating the request is safe.
    """

    def post(self, request):
        serializer = ReportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = request_daily_report(
            serializer.validated_data["snapshot_date"],
            run_id=serializer.validated_data["dag_run_id"],
            dispatch=dispatch_report_generation,
        )

        return Response(ReportRequestResultSerializer(result).data, status=status.HTTP_202_ACCEPTED)
