# Architecture

GitHub Repository Analytics tracks public GitHub repositories and records one metrics snapshot
(stars, forks, open issues) per repository per day.

- A **Django** application owns the data and exposes a REST API.
- An **Airflow** DAG collects the metrics every day, one dynamically mapped task chain per repository.
- A **Django Celery** worker turns each day's snapshots into a report.
- A **Vue** dashboard is the user interface.

Diagrams of everything described here are in [DIAGRAMS.md](DIAGRAMS.md). The reasons behind the
choices are in [DECISIONS.md](DECISIONS.md).

## Services

Everything runs in Docker Compose (`compose.yaml`). Nine containers run permanently and two run
once at start-up.

| Service | Image | Responsibility | Published on |
|---|---|---|---|
| `postgres` | `postgres:18.6` | One server, two databases: `analytics` (Django) and `airflow` (Airflow metadata and Celery results) | not published |
| `redis` | `redis:7.4-alpine` | Message broker for both Celery systems, in separate logical databases | not published |
| `backend-migrate` | backend image | One-shot: applies Django migrations, then exits | |
| `api` | backend image | Django and DRF on gunicorn: the public API for the dashboard and the internal API for the pipeline | `127.0.0.1:8000` |
| `django-worker` | backend image | Django's Celery worker: generates daily reports | not published |
| `frontend` | nginx with the built Vue app | Serves the dashboard and proxies `/api/` to `api` | `127.0.0.1:3000` |
| `airflow-init` | Airflow image | One-shot: migrates the Airflow database and creates the UI login | |
| `airflow-apiserver` | Airflow image | Airflow UI, REST API, and the execution API that workers report to | `127.0.0.1:8080` |
| `airflow-scheduler` | Airflow image | Creates DAG runs on schedule and hands task instances to the executor | not published |
| `airflow-dag-processor` | Airflow image | Parses DAG files (always a separate process in Airflow 3) | not published |
| `airflow-worker` | Airflow image | Airflow's Celery worker: executes every task instance | not published |

The backend image is built from `backend/`, the Airflow image from `airflow/` (the official
`apache/airflow:3.3.2-python3.13` image plus the DAG files), and the frontend image from
`frontend/`. Three more services exist only for running tests (`backend-tests`,
`airflow-tests`, `frontend-tests`) and one for front-end development (`frontend-dev`).

All published ports are bound to the loopback interface. The whole stack uses about 1.4 GB of
memory when idle.

## Who is responsible for what

| Concern | Owner | Notes |
|---|---|---|
| Which repositories are tracked | Django | Managed through the dashboard; stored in `analytics` |
| Whether a repository may be tracked | Django | URL validation and a check against the GitHub API, before anything is stored |
| When collection happens | Airflow scheduler | Daily timetable in the DAG; manual runs from the Airflow UI or CLI |
| Which repositories a run processes | Django, asked by Airflow at run time | The DAG contains no repository names |
| Fetching metrics from GitHub | Airflow worker | One mapped task instance per repository |
| Checking fetched metrics | Airflow worker, then Django again | The worker validates before sending; Django validates what it receives |
| Storing snapshots, keeping them unique per day | Django | The only writer of the `analytics` database |
| Generating the daily report | Django Celery worker | Requested by Airflow, executed asynchronously |
| Status of a DAG run | Airflow | Shown in the Airflow UI |
| Status of a report | Django (`DailyReport.status`) | Shown in the dashboard and at `/api/reports/` |

The last two rows are separate on purpose. A successful DAG run means "metrics collected and
report requested". Whether the report has been generated is a different question with its own
answer.

## Django application structure

The backend is one Django project (`config`) with one app (`analytics`), divided into four layers.
Dependencies point inwards: `api` and `infrastructure` depend on `application`, which depends on
`domain`; `domain` depends on nothing but the standard library.

```
backend/
├── config/                  Django project
│   ├── settings.py          Everything deployment-specific comes from the environment
│   ├── celery.py            Django's Celery application
│   └── urls.py
└── analytics/
    ├── domain/              Business rules. No Django, DRF, requests or Celery imports.
    │   ├── repository_url.py    Parsing GitHub URLs into a validated owner/name pair
    │   ├── reports.py           Report lifecycle and the report calculations
    │   └── errors.py            Named failures shared by every layer
    ├── application/         Use cases: one function per thing the system can do
    │   ├── repositories.py      add_repository, deactivate_repository
    │   ├── snapshots.py         record_snapshot (idempotent)
    │   ├── reports.py           request_daily_report, and the steps of generating one
    │   ├── queries.py           Read side for the dashboard and the pipeline
    │   └── ports.py             GitHubGateway: what use cases need from GitHub
    ├── infrastructure/
    │   └── github_client.py     The only module that talks to GitHub
    ├── api/                 REST adapters
    │   ├── views.py             Public endpoints
    │   ├── internal_views.py    Endpoints for Airflow
    │   ├── serializers.py       Request validation and response shapes
    │   ├── authentication.py    Bearer-token authentication for internal endpoints
    │   └── exception_handler.py The one place that maps failures to HTTP responses
    ├── models.py            Persistence and database constraints only
    ├── tasks.py             Celery task: a thin wrapper around application/reports.py
    └── tests/
```

Rules the layers follow:

- **Models hold no behaviour beyond storage.** They define tables, relationships and constraints.
  They never call GitHub and contain no workflows.
- **Serializers validate shape, not business rules.** They check types and ranges and produce
  response bodies.
- **Views are thin.** A view validates the request, calls one use case, and serialises the result.
- **Use cases use the ORM directly.** There is no repository layer over Django's ORM; it would
  be an abstraction with a single implementation. The one port is `GitHubGateway`, because GitHub
  is the dependency that tests have to replace.
- **Errors are raised by name and translated once.** A use case raises, for example,
  `DuplicateRepository`; only `api/exception_handler.py` knows that this means HTTP 409.

A request through the layers, using "add a repository" as the example:

1. `api/views.py` validates that the body has a `url` and calls `add_repository`.
2. `domain/repository_url.py` parses the URL into `owner` and `name`, or rejects it.
3. `application/repositories.py` checks for an active duplicate, then asks the gateway.
4. `infrastructure/github_client.py` calls `https://api.github.com/repos/{owner}/{name}` and
   translates the HTTP outcome into metadata or a named error.
5. The use case requires the repository to be public and stores it (or reactivates a removed row)
   in a transaction, relying on unique constraints for the concurrent case.
6. The view returns 201 or 200; any error raised on the way becomes one JSON error envelope.

## Communication between services

| From | To | How | Authentication | Purpose |
|---|---|---|---|---|
| Browser | `frontend` | HTTP, port 3000 | none | Dashboard and its API calls |
| `frontend` (nginx) | `api` | HTTP proxy of `/api/`, except `/api/internal/` | none | Public API |
| `api` | GitHub | HTTPS, `GET /repos/{owner}/{name}` | optional `GITHUB_TOKEN` | Validate a repository before tracking it |
| `airflow-worker` | `api` | HTTP, `/api/internal/` | `Authorization: Bearer INTERNAL_API_TOKEN` | Work list, snapshots, report request |
| `airflow-worker` | GitHub | HTTPS, `GET /repos/{owner}/{name}` | optional `GITHUB_TOKEN` | Collect metrics |
| `airflow-worker` | `airflow-apiserver` | HTTP, execution API | short-lived JWT signed with the shared secret | Task state, XCom, heartbeats |
| `airflow-scheduler` | `airflow-worker` | Celery messages through Redis database 0 | network isolation | Run task instances |
| `api` | `django-worker` | Celery messages through Redis database 1 | network isolation | Generate a report |
| `api`, `django-worker` | `postgres` (`analytics`) | PostgreSQL protocol | role `analytics` | Application data |
| Airflow services | `postgres` (`airflow`) | PostgreSQL protocol | role `airflow` | Metadata and Celery results |

Airflow and Django never share a database. Airflow reaches application data only through the
internal API, and Django knows nothing about Airflow beyond accepting those calls.

## Airflow and Celery responsibilities

Three things in this stack are easy to mix up:

- **Airflow scheduling and orchestration** decides when the pipeline runs and in which order its
  tasks execute, and tracks their state, retries and timeouts.
- **Airflow's CeleryExecutor workers** are where those tasks execute. Celery here is Airflow's
  transport; it carries no application logic.
- **Django Celery tasks** are the application's own background work: one task that generates the
  daily report when the pipeline asks for it.

They are described in detail in [AIRFLOW.md](AIRFLOW.md) and [CELERY.md](CELERY.md), including how
the two Celery systems are kept from seeing each other's messages.

## Database and queue design

### PostgreSQL

| Database | Role | Contents |
|---|---|---|
| `analytics` | `analytics` | `analytics_repository`, `analytics_repositorysnapshot`, `analytics_dailyreport` |
| `airflow` | `airflow` | Airflow's metadata tables, and `celery_taskmeta` for the CeleryExecutor's results |

Each role owns its database and `CONNECT` is revoked from everyone else, so neither application
can open the other's data. Both are created by `docker/postgres/init-databases.sh` when the data
volume is first initialised.

Guarantees enforced by the `analytics` schema itself, not only by application code:

| Constraint | Guarantees |
|---|---|
| `Repository.github_id` unique | A GitHub repository is tracked at most once, even after it is renamed |
| `Repository (lower(owner), lower(name))` unique | No duplicates that differ only in letter case |
| `RepositorySnapshot (repository, snapshot_date)` unique | One snapshot per repository per day: retries and repeated runs cannot duplicate data |
| `RepositorySnapshot.repository` is `PROTECT` | A repository with history cannot be hard-deleted by accident |
| Counts are non-negative integers | No negative stars, forks or issues |
| `DailyReport.snapshot_date` unique | One report per day |
| `DailyReport.status` check | Only `queued`, `running`, `completed`, `failed` |

### Redis

| Logical database | Used by | Queue |
|---|---|---|
| 0 | Airflow CeleryExecutor | `airflow_tasks` |
| 1 | Django Celery | `django_reports` |

Redis is a broker only and runs without persistence. Everything durable is in PostgreSQL.

### Idempotency

| Operation | Key | Effect of repeating it |
|---|---|---|
| Add a repository | GitHub id, and case-insensitive owner and name | 409, or reactivation of the removed row |
| Remove a repository | repository id | No change, still 204 |
| Store a snapshot | repository and UTC date of `collected_at` | Updates the day's row if the observation is newer, otherwise nothing |
| Request a report | snapshot date, plus the requesting DAG run | Nothing while in flight or already completed for that run |
| Generate a report | report row and its stored Celery task id | A duplicate or superseded delivery does nothing |

## Security limits of the local setup

This stack is a local demonstration. It is **not production-ready**, and the following are
deliberate limits rather than oversights.

| Area | State in this project | What production would need |
|---|---|---|
| User authentication | None. Anyone who can reach the API can add and remove repositories | Login, per-user authorisation, CSRF protection for cookie-based sessions |
| Network exposure | Ports are bound to `127.0.0.1` only; PostgreSQL and Redis are not published | A reverse proxy with TLS; private networks for data stores |
| Transport security | Plain HTTP everywhere, including the internal API | TLS between all components |
| Internal API | One shared static bearer token, compared in constant time; not routed by nginx | Per-service credentials that can be rotated, or mutual TLS |
| Secrets | Generated into a git-ignored `.env` and passed as environment variables | A secret manager; no secrets in process environments |
| Airflow login | One administrator created at start-up through the FAB auth manager | Single sign-on and role separation |
| Database credentials | One role per application, both created from the same init script | Separately managed roles and servers, backups |
| Rate limiting | `POST /api/repositories/` is throttled per client, in process memory | A shared rate limiter in front of the API |
| Redis | No password, no persistence | Authentication, TLS, and durability appropriate to the workload |
| Django | `DEBUG=false`, but no hardening beyond the defaults | The deployment checklist: HTTPS settings, secure headers, logging and monitoring |

What the project does enforce even locally:

- **No server-side request forgery.** A submitted URL is parsed, never fetched. Requests go only to
  the fixed host `api.github.com`, built from identifiers that passed a strict pattern, and
  redirects are followed for one hop on that same host only.
- **Timeouts on every outbound request**, in Django and in the pipeline.
- **Retries with exponential backoff where nobody is waiting**: in the pipeline's tasks and in the
  report task. The one interactive call, validating a repository when it is added, is made once;
  a failure is reported as a temporary error and the user can simply try again.
- **Bounded load on GitHub**: at most four concurrent requests from the pipeline, and a rate limit
  on the endpoint that makes Django call GitHub.
- **Tokens stay out of logs and out of XCom.** They reach Airflow as connections defined on the
  worker only; neither client logs request headers.
- **Input validation at every boundary**, with consistent status codes.
- **No working secret in the repository.** `.env.example` holds empty placeholders.
- **A Content Security Policy** on the dashboard that allows only same-origin resources.

## Trade-offs

The significant decisions, each with what it costs, are recorded in [DECISIONS.md](DECISIONS.md).
The ones that shape the architecture most:

- Airflow fetches from GitHub itself, and Django remains the only writer of application data
  (decision 11).
- Each repository is a mapped task group, so one failure does not stop the others (decision 10),
  but any failure holds back the day's report (decision 12).
- The DAG does not wait for the report (decision 19), and Django keeps no copy of Airflow's run
  state (decision 20).
- One PostgreSQL server and one Redis server are shared, with isolation by database, role and
  queue instead of by container (decisions 2, 3 and 17).
