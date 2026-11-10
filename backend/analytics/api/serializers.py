"""Request validation and response shapes. No business logic and no external calls."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from analytics.models import DailyReport, Repository, RepositorySnapshot

# PostgreSQL integer columns top out here; reject anything larger before it reaches the database.
_MAX_COUNT = 2_147_483_647
_ALLOWED_CLOCK_SKEW = timedelta(minutes=5)


# --- Public API -----------------------------------------------------------------------------------
class RepositoryCreateSerializer(serializers.Serializer):
    url = serializers.CharField(max_length=2048, trim_whitespace=True)


class LatestSnapshotSerializer(serializers.Serializer):
    """Reads the `latest_*` annotations added by `queries.repositories_with_latest_snapshot`."""

    snapshot_date = serializers.DateField(source="latest_snapshot_date")
    stars = serializers.IntegerField(source="latest_stars")
    forks = serializers.IntegerField(source="latest_forks")
    open_issues = serializers.IntegerField(source="latest_open_issues")
    collected_at = serializers.DateTimeField(source="last_synced_at")


class RepositorySerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    html_url = serializers.CharField(read_only=True)
    last_synced_at = serializers.DateTimeField(read_only=True, allow_null=True)
    latest_snapshot = serializers.SerializerMethodField()

    class Meta:
        model = Repository
        fields = [
            "id",
            "github_id",
            "owner",
            "name",
            "full_name",
            "html_url",
            "is_active",
            "created_at",
            "deactivated_at",
            "last_synced_at",
            "latest_snapshot",
        ]

    def get_latest_snapshot(self, repository) -> dict | None:
        if getattr(repository, "latest_snapshot_date", None) is None:
            return None
        return LatestSnapshotSerializer(repository).data


class SnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = RepositorySnapshot
        fields = ["snapshot_date", "stars", "forks", "open_issues", "collected_at"]


class DailyReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyReport
        fields = [
            "id",
            "snapshot_date",
            "status",
            "requested_at",
            "started_at",
            "finished_at",
            "attempts",
            "error_message",
            "repository_count",
            "total_stars",
            "total_forks",
            "total_open_issues",
            "stars_delta",
            "forks_delta",
            "open_issues_delta",
            "repository_changes",
        ]


class DashboardSummarySerializer(serializers.Serializer):
    active_repositories = serializers.IntegerField()
    repositories_with_data = serializers.IntegerField()
    total_stars = serializers.IntegerField()
    total_forks = serializers.IntegerField()
    total_open_issues = serializers.IntegerField()
    last_synced_at = serializers.DateTimeField(allow_null=True)


class CollectionStatusSerializer(serializers.Serializer):
    last_collected_at = serializers.DateTimeField(allow_null=True)
    latest_snapshot_date = serializers.DateField(allow_null=True)
    repositories_collected = serializers.IntegerField()
    active_repositories = serializers.IntegerField()


class PipelineStatusSerializer(serializers.Serializer):
    collection = CollectionStatusSerializer()
    latest_report = DailyReportSerializer(allow_null=True)


# --- Internal API (Airflow) -----------------------------------------------------------------------
class ActiveRepositorySerializer(serializers.ModelSerializer):
    """Deliberately small: this list travels through Airflow XCom."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Repository
        fields = ["id", "github_id", "owner", "name", "full_name"]


class SnapshotWriteSerializer(serializers.Serializer):
    repository_id = serializers.IntegerField(min_value=1)
    stars = serializers.IntegerField(min_value=0, max_value=_MAX_COUNT)
    forks = serializers.IntegerField(min_value=0, max_value=_MAX_COUNT)
    open_issues = serializers.IntegerField(min_value=0, max_value=_MAX_COUNT)
    collected_at = serializers.DateTimeField()

    def validate_collected_at(self, value):
        if value > timezone.now() + _ALLOWED_CLOCK_SKEW:
            raise serializers.ValidationError("The collection time cannot be in the future.")
        return value


class SnapshotWriteResultSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="snapshot.id")
    repository_id = serializers.IntegerField(source="snapshot.repository_id")
    snapshot_date = serializers.DateField(source="snapshot.snapshot_date")
    outcome = serializers.CharField()


class ReportRequestSerializer(serializers.Serializer):
    snapshot_date = serializers.DateField()
    # Identifies the requesting DAG run, which makes a retried request recognisable.
    dag_run_id = serializers.CharField(max_length=250, required=False, allow_blank=True, default="")


class ReportRequestResultSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="report.id")
    snapshot_date = serializers.DateField(source="report.snapshot_date")
    status = serializers.CharField(source="report.status")
    enqueued = serializers.BooleanField()
