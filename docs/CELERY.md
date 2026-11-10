# Celery in this project

Celery appears twice in this stack, in two unrelated roles. Keeping them apart is one of the
design goals, so this document starts with the distinction and then covers the Django side in
detail. The Airflow side is covered in [AIRFLOW.md](AIRFLOW.md).

## Three things that are easy to confuse

| | What it is | Who decides what runs | Where it runs |
|---|---|---|---|
| **Airflow scheduling and orchestration** | The scheduler turns the DAG's timetable and task dependencies into task instances, tracks their state and retries | The DAG definition | `airflow-scheduler` (with `airflow-dag-processor` and `airflow-apiserver`) |
| **Airflow CeleryExecutor workers** | The transport Airflow uses to run those task instances on other machines. Celery here is plumbing: the only Celery task is Airflow's own `execute_workload` | The Airflow scheduler | `airflow-worker` |
| **Django Celery tasks** | The application's own background job, `analytics.generate_daily_report` | The Django application, when Airflow asks for a report | `django-worker` |

Airflow decides **when** things happen and in **what order**. Its Celery workers are where the
pipeline's tasks execute. Django's Celery worker does a piece of application work that the
pipeline requests but does not wait for.

There is **no Celery Beat** anywhere. Nothing in Django runs on a timer: the daily rhythm belongs
to Airflow alone, so there is exactly one place that defines the schedule.

## How the two Celery systems are kept apart

They share one Redis container, and nothing else.

| | Airflow CeleryExecutor | Django Celery |
|---|---|---|
| Celery application | `airflow.providers.celery.executors.celery_executor` | `analytics` (`backend/config/celery.py`) |
| Broker | `redis://redis:6379/0` | `redis://redis:6379/1` |
| Queue | `airflow_tasks` | `django_reports` |
| Worker container | `airflow-worker` (`airflow celery worker`) | `django-worker` (`celery --app=config worker --queues=django_reports`) |
| Worker node name | `celery@<hostname>` | `django@<hostname>` |
| Registered tasks | `execute_workload` | `analytics.generate_daily_report` |
| Result backend | PostgreSQL, database `airflow` (`db+postgresql://`) | none |
| Image | `apache/airflow:3.3.2` plus the DAGs | the Django backend image |

Three independent barriers follow from this table:

1. **Different Redis databases.** Kombu stores queues and its bookkeeping keys per Redis
   database, so a worker connected to database 0 cannot see anything in database 1.
2. **Different queue names.** Even on one database, each worker subscribes only to its own queue.
3. **Different task registries.** Neither worker has the other's code, so a misrouted message
   would be rejected as an unknown task rather than executed.

Both sides run the same Celery, kombu and redis-py versions. They are pinned in
`backend/requirements.in` to the versions in Airflow's constraints file for 3.3.2.

### Checking the isolation yourself

```bash
# Each Redis database holds only its own system's keys.
docker compose exec redis redis-cli -n 0 --scan    # _kombu.binding.airflow_tasks, ...
docker compose exec redis redis-cli -n 1 --scan    # _kombu.binding.django_reports, ...

# Each worker consumes only its own queue.
docker compose exec airflow-worker celery \
  --app airflow.providers.celery.executors.celery_executor.app inspect active_queues
docker compose exec django-worker celery --app=config inspect active_queues

# Stop one worker and trigger a run: the other one never picks up its messages.
docker compose stop django-worker
docker compose exec airflow-scheduler airflow dags trigger github_metrics_daily
docker compose exec redis redis-cli -n 1 llen django_reports    # 1: the report waits
docker compose start django-worker                              # ... and is generated
```

With `django-worker` stopped, the DAG run still succeeds and the dashboard shows the report as
**Queued** until the worker is started again. That is the clearest way to see that the report's
status and the DAG run's status are two separate things.

## Django integration

The setup is Celery's standard Django integration:

- `backend/config/celery.py` creates the application, loads every `CELERY_*` setting from
  `config/settings.py` (`config_from_object("django.conf:settings", namespace="CELERY")`) and
  discovers tasks in the `tasks` module of each installed app.
- `backend/config/__init__.py` imports that application, so `@shared_task` functions bind to it in
  the web process as well as in the worker.
- `backend/analytics/tasks.py` holds the task and the function that publishes it.

Settings that matter:

| Setting | Value | Why |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://redis:6379/1` | Django's own Redis database |
| `CELERY_TASK_DEFAULT_QUEUE` | `django_reports` | A queue name that cannot collide with Airflow's |
| `CELERY_TASK_IGNORE_RESULT` | `True` | The outcome is stored on the `DailyReport` row; a result backend would duplicate it |
| `CELERY_TASK_ACKS_LATE`, `CELERY_TASK_REJECT_ON_WORKER_LOST` | `True` | A message is acknowledged only after the task finished, so a crashed worker does not lose a report |
| `CELERY_WORKER_PREFETCH_MULTIPLIER` | `1` | A worker process holds one message at a time |
| `CELERY_TASK_SOFT_TIME_LIMIT` / `CELERY_TASK_TIME_LIMIT` | 60 s / 90 s | A stuck generation is stopped and marked failed |
| `CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]` | 600 s | Longer than the time limit, so a message is redelivered only when its worker is really gone |
| `REPORT_STALE_AFTER_SECONDS` | 900 | After this long in `queued` or `running`, a report may be requested again |

## The report task

`generate_daily_report(report_id)` does the following:

1. **Start.** Lock the `DailyReport` row and move it from `queued` to `running`. The task only
   proceeds if the row's stored task id equals its own; otherwise the delivery is a duplicate or
   has been superseded, and the task ends without doing anything.
2. **Read.** Load every snapshot of the report's date, each with its repository's previous
   available snapshot (the most recent earlier one, however old), in one query.
3. **Calculate.** `analytics.domain.reports.summarize` turns them into totals, changes and one
   line per repository. This step is pure Python and is unit tested without a database.
4. **Store.** Write the results and set the status to `completed`.

What is calculated:

- **Totals** add up the current stars, forks and open issues of every repository in the report.
- **A repository's change** is its current value minus its previous available snapshot. A
  repository collected for the first time has no change (`null`), not a change from zero.
- **Aggregate changes** add up only the repositories that have a previous snapshot, so adding a
  popular repository does not look like tens of thousands of stars gained in a day.

## Status, failures and retries

The row's `status` is the single source of truth, and every transition is made in
`backend/analytics/application/reports.py`. The allowed transitions are defined once, in
`backend/analytics/domain/reports.py`.

| Status | Meaning | Set when |
|---|---|---|
| `queued` | A message is waiting for a worker, or a retry is scheduled | The request is accepted; a temporary error occurred |
| `running` | A worker is generating the report | The task starts |
| `completed` | Results are stored | Generation finished |
| `failed` | Gave up; `error_message` says why | Unexpected error, retries used up, time limit, or the message could not be published |

| Failure | What happens |
|---|---|
| Temporary database error (`OperationalError`, `InterfaceError`) | Status back to `queued` with the reason recorded; retried after 5 s, 10 s and 20 s; `failed` after the fourth attempt |
| Any other exception | `failed` immediately with the exception recorded; retrying a bug would only repeat it |
| Time limit exceeded | `failed` |
| The worker dies mid-task | The message was not acknowledged, so the broker redelivers it; the task id still matches the row, so generation starts again |
| The message is delivered twice | The second delivery finds the report `completed` and ends without doing anything |
| The broker is unreachable when the report is requested | The row is marked `failed`, the API answers 503, and Airflow's retry of the request queues it again |
| The message is lost (Redis restarted) | The row stays `queued`; after 15 minutes it counts as stale and the next request queues it again |

`attempts` on the row counts how often generation started.

## Idempotency of report requests

`POST /api/internal/reports/` can be repeated safely, which matters because Airflow retries the
task that calls it.

- There is **one row per snapshot date**, guaranteed by a unique constraint.
- The request **chooses the Celery task id**, stores it on the row as `queued`, commits, and only
  then publishes the message. A worker can therefore never start before the row knows which task
  owns it, and a publish failure cannot leave a row claiming `queued` without a message.
- While a report is `queued` or `running`, another request changes nothing.
- A `completed` report is left alone when the **same DAG run** asks again, and regenerated when a
  **different run** asks, because that run collected newer snapshots for the day.

The full table of cases is in [API.md](API.md#post-apiinternalreports).

## Watching it work

```bash
docker compose logs --follow django-worker
```

A successful generation looks like this:

```text
Task analytics.generate_daily_report[b807f61f-...] received
analytics.tasks Generating report for 2026-11-10 (attempt 1)
analytics.tasks Report for 2026-11-10 completed: 3 repositories
Task analytics.generate_daily_report[b807f61f-...] succeeded in 0.05s: None
```

The same information is in the dashboard ("Latest daily report" and the "Daily reports" table)
and at `GET /api/reports/`.
