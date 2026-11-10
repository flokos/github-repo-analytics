"""Persistence models.

Models describe storage and database-level guarantees only. Business rules live in
`analytics.domain`, workflows in `analytics.application`, and no model calls an external API.
"""

from django.db import models
from django.db.models.functions import Lower

from .domain.reports import ReportStatus


class Repository(models.Model):
    """A GitHub repository the user asked to track.

    Removing a repository only clears `is_active`, so its snapshots stay available and it can be
    reactivated later without creating a second row.
    """

    github_id = models.BigIntegerField(
        unique=True, help_text="GitHub's numeric repository id. It survives renames and transfers."
    )
    owner = models.CharField(max_length=39)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "repositories"
        ordering = ["id"]
        constraints = [
            # GitHub treats owner and repository names case-insensitively.
            models.UniqueConstraint(
                Lower("owner"), Lower("name"), name="repository_owner_name_ci_uniq"
            ),
        ]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


class RepositorySnapshot(models.Model):
    """The metrics of one repository as observed on one UTC calendar day."""

    repository = models.ForeignKey(Repository, on_delete=models.PROTECT, related_name="snapshots")
    snapshot_date = models.DateField(help_text="UTC date on which the metrics were observed.")
    stars = models.PositiveIntegerField()
    forks = models.PositiveIntegerField()
    open_issues = models.PositiveIntegerField(
        help_text="As reported by GitHub, whose count also includes open pull requests."
    )
    collected_at = models.DateTimeField(help_text="When GitHub was queried.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["snapshot_date"]
        constraints = [
            # One measurement per repository per day. This is what makes retries and repeated
            # pipeline runs idempotent even under concurrency.
            models.UniqueConstraint(
                fields=["repository", "snapshot_date"], name="snapshot_repository_date_uniq"
            ),
        ]
        indexes = [models.Index(fields=["snapshot_date"], name="snapshot_date_idx")]

    def __str__(self) -> str:
        return f"{self.repository} @ {self.snapshot_date}"


class DailyReport(models.Model):
    """Aggregated metrics and day-over-day changes for one snapshot date.

    `status` tracks the asynchronous Django Celery job that fills the result fields. It is
    independent of the state of the Airflow DAG run that requested the report.
    """

    snapshot_date = models.DateField(unique=True)
    status = models.CharField(
        max_length=16,
        choices=[(status.value, status.name.title()) for status in ReportStatus],
        default=ReportStatus.QUEUED.value,
    )
    requested_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger_run_id = models.CharField(
        max_length=250, blank=True, help_text="Airflow DAG run that last requested this report."
    )
    celery_task_id = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True)

    # Results, written when the report completes.
    repository_count = models.PositiveIntegerField(default=0)
    total_stars = models.BigIntegerField(default=0)
    total_forks = models.BigIntegerField(default=0)
    total_open_issues = models.BigIntegerField(default=0)
    stars_delta = models.BigIntegerField(default=0)
    forks_delta = models.BigIntegerField(default=0)
    open_issues_delta = models.BigIntegerField(default=0)
    repository_changes = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-snapshot_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=[status.value for status in ReportStatus]),
                name="daily_report_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Report {self.snapshot_date} ({self.status})"
