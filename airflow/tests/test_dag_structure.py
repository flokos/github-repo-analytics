"""What the DAG file declares: schedule, dependencies, mapping and failure handling.

These tests parse the real DAG file the way the DAG processor does. They need no scheduler,
no worker and no network.
"""

from datetime import timedelta

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.sdk import CronTriggerTimetable, RetryAction, TriggerRule
from helpers import DAG_ID, DAGS_FOLDER

from github_analytics import errors

MAPPED_GROUP = "collect_repository"
FETCH = f"{MAPPED_GROUP}.fetch_metrics"
VALIDATE = f"{MAPPED_GROUP}.validate_metrics"
PERSIST = f"{MAPPED_GROUP}.persist_snapshot"


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=str(DAGS_FOLDER))


@pytest.fixture(scope="module")
def dag(dagbag):
    return dagbag.dags[DAG_ID]


# --- Import ---------------------------------------------------------------------------------------
def test_dag_file_imports_without_errors(dagbag):
    assert dagbag.import_errors == {}


def test_exactly_one_dag_is_defined_and_the_helper_package_is_not_parsed(dagbag):
    assert list(dagbag.dags) == [DAG_ID]


def test_parsing_makes_no_network_or_connection_calls(http, monkeypatch):
    """Parsing must work with no connections defined and with every HTTP request forbidden."""
    monkeypatch.delenv("AIRFLOW_CONN_GITHUB_API", raising=False)
    monkeypatch.delenv("AIRFLOW_CONN_DJANGO_INTERNAL_API", raising=False)

    bag = DagBag(dag_folder=str(DAGS_FOLDER))

    assert bag.import_errors == {}
    assert len(http.calls) == 0


# --- Schedule -------------------------------------------------------------------------------------
def test_runs_automatically_every_day_at_six_utc(dag):
    assert isinstance(dag.timetable, CronTriggerTimetable)
    assert dag.timetable.expression == "0 6 * * *"
    assert str(dag.timetable.timezone) == "UTC"


def test_schedule_is_timezone_aware_and_does_not_backfill(dag):
    assert dag.start_date.utcoffset() == timedelta(0)
    assert dag.catchup is False


def test_starts_unpaused_so_no_manual_step_is_needed(dag):
    assert dag.is_paused_upon_creation is False


def test_runs_never_overlap_and_are_bounded_in_time(dag):
    assert dag.max_active_runs == 1
    assert dag.dagrun_timeout == timedelta(minutes=30)


def test_schedule_can_be_overridden_from_the_environment(monkeypatch):
    monkeypatch.setenv("PIPELINE_SCHEDULE_CRON", "*/5 * * * *")
    monkeypatch.setenv("PIPELINE_SCHEDULE_TIMEZONE", "Europe/Athens")

    timetable = DagBag(dag_folder=str(DAGS_FOLDER)).dags[DAG_ID].timetable

    assert timetable.expression == "*/5 * * * *"
    assert str(timetable.timezone) == "Europe/Athens"


# --- Tasks and dependencies -----------------------------------------------------------------------
def test_tasks(dag):
    assert set(dag.task_ids) == {
        "load_active_repositories",
        FETCH,
        VALIDATE,
        PERSIST,
        "trigger_daily_report",
    }


def test_dependencies(dag):
    upstream = {task.task_id: task.upstream_task_ids for task in dag.tasks}

    assert upstream["load_active_repositories"] == set()
    assert upstream[FETCH] == {"load_active_repositories"}
    assert upstream[VALIDATE] == {"load_active_repositories", FETCH}
    assert upstream[PERSIST] == {VALIDATE}
    assert upstream["trigger_daily_report"] == {PERSIST}


# --- Dynamic task mapping -------------------------------------------------------------------------
@pytest.mark.parametrize("task_id", [FETCH, VALIDATE, PERSIST])
def test_per_repository_tasks_live_in_a_mapped_task_group(dag, task_id):
    group = dag.get_task(task_id).task_group

    assert group.group_id == MAPPED_GROUP
    assert type(group).__name__ == "MappedTaskGroup"


@pytest.mark.parametrize("task_id", ["load_active_repositories", "trigger_daily_report"])
def test_run_level_tasks_are_not_mapped(dag, task_id):
    assert type(dag.get_task(task_id).task_group).__name__ == "TaskGroup"


def test_the_mapping_input_is_the_repository_list_loaded_at_run_time(dag):
    """Nothing is hard-coded: the group expands over the output of the first task."""
    group = dag.get_task(FETCH).task_group

    assert {task.task_id for task in group.iter_mapped_dependencies()} == {
        "load_active_repositories"
    }


@pytest.mark.parametrize("task_id", [FETCH, VALIDATE, PERSIST])
def test_mapped_instances_are_labelled_with_the_repository_name(dag, task_id):
    assert dag.get_task(task_id).map_index_template == "{{ repository_label }}"


def test_concurrent_github_requests_are_bounded(dag):
    assert dag.get_task(FETCH).max_active_tis_per_dagrun == 4


# --- Retries, timeouts and failure handling -------------------------------------------------------
@pytest.mark.parametrize(
    "task_id", ["load_active_repositories", FETCH, PERSIST, "trigger_daily_report"]
)
def test_io_tasks_retry_with_exponential_backoff(dag, task_id):
    task = dag.get_task(task_id)

    assert task.retries == 3
    assert task.retry_delay == timedelta(seconds=30)
    assert task.retry_exponential_backoff == 2.0
    assert task.max_retry_delay == timedelta(minutes=5)


def test_validation_is_never_retried(dag):
    assert dag.get_task(VALIDATE).retries == 0


def test_every_task_has_an_execution_timeout(dag):
    assert all(task.execution_timeout == timedelta(minutes=2) for task in dag.tasks)


PERMANENT_ERRORS = [
    errors.RepositoryUnavailableError,
    errors.GitHubAuthError,
    errors.RateLimitExhaustedError,
    errors.InvalidMetricsError,
    errors.InternalApiAuthError,
    errors.InternalApiRejectedError,
]
TRANSIENT_ERRORS = [
    errors.RateLimitedError,
    errors.GitHubUnavailableError,
    errors.InternalApiUnavailableError,
    TimeoutError,
    RuntimeError,
]


@pytest.mark.parametrize("error", PERMANENT_ERRORS)
def test_permanent_errors_fail_at_once_without_using_retries(dag, error):
    for task in dag.tasks:
        decision = task.retry_policy.evaluate(error("boom"), try_number=1, max_tries=3)
        assert decision.action is RetryAction.FAIL


@pytest.mark.parametrize("error", TRANSIENT_ERRORS)
def test_other_errors_go_through_the_normal_retries(dag, error):
    for task in dag.tasks:
        decision = task.retry_policy.evaluate(error("boom"), try_number=1, max_tries=3)
        assert decision.action is RetryAction.DEFAULT


def test_report_is_triggered_when_nothing_failed_including_when_everything_was_skipped(dag):
    """NONE_FAILED: runs after all-success and after an empty (skipped) mapping, never after a
    failure. Every other task keeps the default rule."""
    rules = {task.task_id: task.trigger_rule for task in dag.tasks}

    assert rules.pop("trigger_daily_report") == TriggerRule.NONE_FAILED
    assert set(rules.values()) == {TriggerRule.ALL_SUCCESS}
