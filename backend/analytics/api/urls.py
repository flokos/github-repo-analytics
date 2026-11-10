from django.urls import path

from . import internal_views, views

urlpatterns = [
    # Public: used by the dashboard.
    path("health/", views.HealthView.as_view(), name="health"),
    path("repositories/", views.RepositoryListCreateView.as_view(), name="repository-list"),
    path("repositories/<int:pk>/", views.RepositoryDetailView.as_view(), name="repository-detail"),
    path(
        "repositories/<int:pk>/snapshots/",
        views.RepositorySnapshotsView.as_view(),
        name="repository-snapshots",
    ),
    path("dashboard/summary/", views.DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("reports/", views.ReportListView.as_view(), name="report-list"),
    path("pipeline/status/", views.PipelineStatusView.as_view(), name="pipeline-status"),
    # Internal: used by Airflow, protected by the internal bearer token.
    path(
        "internal/repositories/active/",
        internal_views.ActiveRepositoriesView.as_view(),
        name="internal-active-repositories",
    ),
    path(
        "internal/snapshots/",
        internal_views.SnapshotIngestView.as_view(),
        name="internal-snapshots",
    ),
]
