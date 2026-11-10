# Architecture

GitHub Repository Analytics tracks public GitHub repositories and records one metrics snapshot per
repository per day.

- A **Django** application owns the data and exposes a REST API.
- An **Airflow** DAG collects the metrics every day.
- A **Django Celery** worker turns each day's snapshots into a report.
- A **Vue** dashboard is the user interface.

## Data stores

| Store | Used for | Notes |
|---|---|---|
| PostgreSQL database `analytics` | Repositories, daily snapshots, daily reports | Owned by the `analytics` role |
| PostgreSQL database `airflow` | Airflow metadata and CeleryExecutor task results | Owned by the `airflow` role |
| Redis database 0 | Airflow CeleryExecutor broker | Queue `airflow_tasks` |
| Redis database 1 | Django Celery broker | Queue `django_reports` |

The two PostgreSQL roles cannot connect to each other's database, and Redis holds no durable state.
The reasoning is in [DECISIONS.md](DECISIONS.md).
