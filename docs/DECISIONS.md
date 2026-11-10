# Architectural decisions

Each entry records what was decided, why, and what it costs. Entries are numbered in the order the
decisions were made.

## 1. Version policy: newest release line that already has patch releases

**Decision.** For every component, use the newest release line that (a) has shipped at least one
patch release and (b) every other component in the stack officially declares support for.
Versions were checked against PyPI, npm, Docker Hub and the Airflow repository on 2026-11-10.

| Component | Version | Reason |
|---|---|---|
| Apache Airflow | 3.3.2 (`apache/airflow:3.3.2-python3.13`) | 3.4.0 was four days old and includes an XCom storage migration; 3.3 had two patch releases |
| Airflow Celery provider | 3.24.0 | The version in Airflow's `constraints-3.3.2`, shipped inside the official image |
| Celery, kombu, redis-py | 5.6.3, 5.6.2, 6.4.0 | Django uses exactly the versions Airflow's constraints file tests, so both Celery systems run the same stack |
| Python | 3.13 | Default of the Airflow image and supported by every backend dependency |
| Django | 5.2 LTS | Supported until April 2028; `django-cors-headers` and `pytest-django` did not yet declare support for 6.1 |
| Django REST Framework | 3.18 | Declares support for Django 5.2 |
| PostgreSQL | 18 | Inside Airflow 3.3's tested range (14 to 18) and Django 5.2's (14+) |
| Redis | 7.4 | Same major version as Airflow's reference Compose file; used only as a broker |
| Vue, Vite | 3.5, 8 | Current stable lines; Vitest 5 lists Vite 8 as a supported peer |
| Node.js | 24 LTS | Build and test time only; the running frontend is static files behind nginx |
| GitHub REST API | `2026-03-10` | Newest supported version; sent explicitly in `X-GitHub-Api-Version` |

**Trade-off.** The stack is one minor release behind on Airflow and one major release behind on
Django. Both are single-line upgrades (`AIRFLOW_VERSION` build argument, `requirements.in`).
Exact pins live in `backend/requirements.txt`, `frontend/package-lock.json` and the image tags.

## 2. One PostgreSQL instance, two databases, two roles

**Decision.** A single PostgreSQL container hosts `analytics` (Django) and `airflow` (Airflow
metadata plus the CeleryExecutor result backend). Each has its own login role, and `CONNECT` is
revoked from `PUBLIC`, so the Airflow role cannot open the Django database and vice versa.

**Why.** One container keeps the local stack light while still giving real isolation between the
two applications. The init script runs once, when the data volume is created.

**Trade-off.** Both applications share one server's resources and one failure domain. In
production they would normally be separate managed databases.

## 3. One Redis instance, two logical databases

**Decision.** Redis database 0 is the Airflow CeleryExecutor broker and database 1 is the Django
Celery broker. Persistence is disabled.

**Why.** Kombu keeps its queue lists and bookkeeping keys per Redis database, so two databases give
two independent brokers without a second container. Durable state (snapshots, report status, Airflow
task state) lives in PostgreSQL, so losing Redis only loses messages in flight.

**Trade-off.** A restart of Redis can drop a queued report request or a queued Airflow task.
Airflow reschedules tasks whose messages were lost, and a stale report can be requested again
(see the report idempotency rules in `docs/CELERY.md`).

## 4. Secrets are generated locally, never committed

**Decision.** `.env.example` contains only empty secrets. `scripts/bootstrap-env.sh` creates `.env`
and fills each one with a random value. Compose refuses to start when a required secret is missing.

**Why.** No working credential exists in the repository, not even a "default development password",
and every clone gets its own secrets.

**Trade-off.** One extra first-run command, and the script needs `openssl` and a POSIX shell.
On Windows it runs in WSL or Git Bash.

## 5. One Django app with four thin layers

**Decision.** The backend is a single Django app, `analytics`, split into `domain` (pure Python
rules), `application` (use cases), `infrastructure` (the GitHub HTTP client) and `api` (DRF views,
serializers, authentication, error mapping). Use cases work with the Django ORM directly; the only
port-and-adapter pair is `GitHubGateway`, because GitHub is the one dependency that has to be
replaced in tests.

**Why.** It keeps external calls and workflows out of models and serializers without inventing
abstractions that have a single implementation. A repository pattern over the ORM would add a layer
with nothing behind it.

**Trade-off.** The application layer depends on Django models, so it is not framework-independent.
For a project of this size that is the pragmatic place to stop.

## 6. A repository is identified by GitHub's numeric id, and removal is a soft delete

**Decision.** `Repository.github_id` is unique, and so is the case-insensitive `(owner, name)` pair.
Removing a repository sets `is_active = false`; adding it again reactivates the same row after
GitHub has confirmed it is still public. Snapshots reference repositories with `PROTECT`.

**Why.** The numeric id survives renames and transfers, so the same repository pasted under its new
address is recognised as a duplicate. Soft deletion keeps history available and makes reactivation
a state change instead of a second row.

**Trade-off.** Stored owner and name can go stale after a rename until the repository is re-added.
The pipeline detects the mismatch through the id check described in decision 10.

## 7. Repository URLs are parsed, never fetched

**Decision.** The backend extracts `owner` and `name` from the submitted URL, validates them against
GitHub's naming rules, and builds its own request to the fixed host `api.github.com`. Redirects are
not followed automatically; a single hop is allowed only when it stays on that host (GitHub answers
301 for renamed repositories).

**Why.** User input can only ever choose two path segments from a restricted alphabet, which removes
server-side request forgery as a class of problem instead of filtering for it.

**Trade-off.** Only `https://github.com/<owner>/<name>` (optionally with `www.`, a trailing slash or
`.git`) is accepted. Deeper links such as `/tree/main` are rejected with an explanatory message.

## 8. One error envelope, one mapping from failures to HTTP statuses

**Decision.** Use cases raise named domain errors. `analytics/api/exception_handler.py` is the only
place that turns them into HTTP responses, all shaped as `{"error": {"code", "message", "details"}}`.
A GitHub 404 becomes 422 `repository_unavailable` with a message that never claims the repository
does not exist (GitHub answers 404 for private repositories too). Upstream rate limits become 503
with `Retry-After`, timeouts 504, other GitHub failures 502.

**Why.** The dashboard switches on stable codes, and the HTTP semantics stay honest: 4xx means the
request cannot succeed as sent, 5xx means trying again later may work.

**Trade-off.** 422 for "GitHub does not show this repository" is a judgement call; 404 would also be
defensible but would describe our endpoint, not the submitted repository.

## 9. A snapshot's date is the UTC date of collection, chosen by the server

**Decision.** `POST /api/internal/snapshots/` receives `collected_at` and derives `snapshot_date`
from it. `(repository, snapshot_date)` is unique. A newer observation on the same day replaces the
stored values; an identical or older one changes nothing.

**Why.** GitHub exposes only current counts. If the pipeline could choose the date, re-running an
old DAG run would write today's numbers into the past. Deriving the date from the observation makes
retries, repeated runs and late deliveries all safe.

**Trade-off.** History cannot be backfilled, which is also why the DAG runs with `catchup=False`.
