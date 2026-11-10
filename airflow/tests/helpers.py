"""Builders shared by the test modules."""

from __future__ import annotations

import os
from pathlib import Path

DAGS_FOLDER = Path(os.environ.get("AIRFLOW__CORE__DAGS_FOLDER", "/opt/airflow/dags"))
DAG_ID = "github_metrics_daily"

GITHUB_API = "https://api.github.com"
DJANGO_API = "http://api:8000"

ACTIVE_REPOSITORIES_URL = f"{DJANGO_API}/api/internal/repositories/active/"
SNAPSHOTS_URL = f"{DJANGO_API}/api/internal/snapshots/"
REPORTS_URL = f"{DJANGO_API}/api/internal/reports/"

DJANGO = {
    "id": 1,
    "github_id": 4164482,
    "owner": "django",
    "name": "django",
    "full_name": "django/django",
}
AIRFLOW = {
    "id": 2,
    "github_id": 33884891,
    "owner": "apache",
    "name": "airflow",
    "full_name": "apache/airflow",
}


def github_url(repository: dict) -> str:
    return f"{GITHUB_API}/repos/{repository['owner']}/{repository['name']}"


def github_payload(repository: dict, **overrides) -> dict:
    """The parts of GitHub's "get a repository" response that the pipeline reads."""
    return {
        "id": repository["github_id"],
        "name": repository["name"],
        "full_name": repository["full_name"],
        "private": False,
        "visibility": "public",
        "stargazers_count": 1200,
        "forks_count": 340,
        "open_issues_count": 56,
        **overrides,
    }
