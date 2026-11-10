"""
### Daily GitHub metrics collection

Collects stars, forks and open issues for every repository tracked in the dashboard and stores
one snapshot per repository per UTC day.

| Step | Task | What it does |
|---|---|---|
| 1 | `load_active_repositories` | Asks Django which repositories are active right now |
| 2 | `collect_repository.fetch_metrics` | Reads one repository's metrics from GitHub |
| 3 | `collect_repository.validate_metrics`, `collect_repository.persist_snapshot` | Checks the metrics, then stores them through Django |
| 4 | `trigger_daily_report` | Asks Django to generate the daily report |

Steps 2 and 3 run once **per repository** through dynamic task mapping: the repository list comes
from Django at run time, so adding or removing a repository in the dashboard never requires
touching this file.

**Outcome rules**

- Every repository succeeds: the report is requested and the run succeeds.
- Any repository fails: its siblings still store their snapshots, the report is *not* requested,
  and the run fails.
- No active repositories: the mapped tasks and the report task are skipped and the run succeeds.

**This run's status is not the report's status.** Step 4 succeeds as soon as Django has
*accepted* the request. The report is generated afterwards by a Django Celery worker and has its
own lifecycle (queued, running, completed, failed), visible in the dashboard and at `/api/reports/`.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import pendulum
from airflow.sdk import (
    Connection,
    CronTriggerTimetable,
    ExceptionRetryPolicy,
    RetryAction,
    RetryRule,
    TriggerRule,
    dag,
    get_current_context,
    task,
    task_group,
)
from airflow.sdk.exceptions import AirflowSkipException

from github_analytics import pipeline
from github_analytics.clients import GitHubClient, InternalApiClient
from github_analytics.errors import PermanentError
from github_analytics.validation import validate_metrics as check_metrics

DAG_ID = "github_metrics_daily"

# Connections are supplied to the worker as AIRFLOW_CONN_* environment variables and resolved
# inside tasks, never while this file is parsed. Airflow masks their passwords in task logs.
GITHUB_CONN_ID = "github_api"
DJANGO_CONN_ID = "django_internal_api"

# Read from the environment at parse time: no network or database access happens here.
SCHEDULE_CRON = os.environ.get("PIPELINE_SCHEDULE_CRON", "0 6 * * *")
SCHEDULE_TIMEZONE = os.environ.get("PIPELINE_SCHEDULE_TIMEZONE", "UTC")

# Upper bound on simultaneous GitHub requests. Together with max_active_runs=1 it holds for the
# whole deployment, not just for one run.
MAX_PARALLEL_GITHUB_REQUESTS = 4

# Errors that a retry cannot fix (repository gone, token rejected, invalid metrics) fail the task
# at once. Everything else falls through to the retries and backoff in DEFAULT_ARGS.
FAIL_FAST_ON_PERMANENT_ERRORS = ExceptionRetryPolicy(
    rules=[
        RetryRule(
            exception=PermanentError,
            action=RetryAction.FAIL,
            reason="Permanent error: retrying cannot succeed",
        ),
    ],
)

DEFAULT_ARGS = {
    "owner": "analytics",
    "retries": 3,
    "retry_delay": timedelta(seconds=30),
    "retry_exponential_backoff": 2.0,  # 30 s, 60 s, 120 s
    "max_retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=2),
    "retry_policy": FAIL_FAST_ON_PERMANENT_ERRORS,
}

# Shows "owner/name" instead of a bare index for each mapped task instance in the Airflow UI.
REPOSITORY_LABEL = "{{ repository_label }}"


def _label_task_instance(full_name: str) -> None:
    get_current_context()["repository_label"] = full_name


def _base_url(connection: Any) -> str:
    port = f":{connection.port}" if connection.port else ""
    return f"{connection.schema or 'https'}://{connection.host}{port}"


def _github_client() -> GitHubClient:
    connection = Connection.get(GITHUB_CONN_ID)
    return GitHubClient(_base_url(connection), token=connection.password or "")


def _internal_api_client() -> InternalApiClient:
    connection = Connection.get(DJANGO_CONN_ID)
    return InternalApiClient(_base_url(connection), token=connection.password or "")


@dag(
    dag_id=DAG_ID,
    description="Collects daily GitHub metrics for every tracked repository.",
    schedule=CronTriggerTimetable(SCHEDULE_CRON, timezone=SCHEDULE_TIMEZONE),
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    # GitHub only exposes current values, so past days cannot be collected after the fact.
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    is_paused_upon_creation=False,
    default_args=DEFAULT_ARGS,
    tags=["github", "analytics"],
    doc_md=__doc__,
)
def github_metrics_daily():
    @task
    def load_active_repositories() -> list[dict[str, Any]]:
        """Step 1. The list returned here is what the next tasks are mapped over."""
        return pipeline.load_active_repositories(_internal_api_client())

    @task_group
    def collect_repository(repository: dict[str, Any]):
        """Steps 2 and 3 for one repository. Mapping the group keeps each repository's chain
        independent: one failure does not stop the others from being stored."""

        @task(
            map_index_template=REPOSITORY_LABEL,
            max_active_tis_per_dagrun=MAX_PARALLEL_GITHUB_REQUESTS,
        )
        def fetch_metrics(repository: dict[str, Any]) -> dict[str, Any]:
            _label_task_instance(repository["full_name"])
            return pipeline.fetch_metrics(_github_client(), repository)

        # Validation is deterministic, so retrying it would only repeat the same answer.
        @task(map_index_template=REPOSITORY_LABEL, retries=0)
        def validate_metrics(repository: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
            _label_task_instance(repository["full_name"])
            return check_metrics(repository, metrics)

        @task(map_index_template=REPOSITORY_LABEL)
        def persist_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
            _label_task_instance(snapshot["full_name"])
            return pipeline.persist_snapshot(_internal_api_client(), snapshot)

        return persist_snapshot(validate_metrics(repository, fetch_metrics(repository)))

    # NONE_FAILED: run when every repository succeeded *or* the mapped tasks were skipped because
    # there were no repositories; never when any of them failed.
    @task(trigger_rule=TriggerRule.NONE_FAILED)
    def trigger_daily_report(results: Any) -> list[dict[str, Any]]:
        """Step 4. Succeeds once Django has accepted the request; does not wait for the report."""
        stored = pipeline.as_result_list(results)
        if not stored:
            raise AirflowSkipException("No active repositories: there is nothing to report on.")
        dag_run_id = get_current_context()["dag_run"].run_id
        return pipeline.request_daily_reports(_internal_api_client(), stored, dag_run_id)

    repositories = load_active_repositories()
    results = collect_repository.expand(repository=repositories)
    trigger_daily_report(results)


github_metrics_daily()
