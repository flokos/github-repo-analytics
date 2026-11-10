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

## 10. Each repository is processed by its own mapped task group

**Decision.** The DAG maps a task group, `collect_repository`, over the list of active repositories
that Django returns at run time. Inside the group, `fetch_metrics`, `validate_metrics` and
`persist_snapshot` run in sequence for one repository. `validate_metrics` checks, among other
things, that the id GitHub returns equals the `github_id` stored when the repository was added.

**Why.** Mapping the group (rather than three separately mapped tasks) keeps each repository's
chain independent: when one fetch fails, only that repository's later steps are affected and the
others still store their snapshots. The three steps are separate tasks because they fail
differently: fetching is retried, validation is deterministic and never retried, persisting is
retried and idempotent. The id check catches a renamed repository whose old name was taken over by
a different one, which would otherwise silently record someone else's numbers.

**Trade-off.** Three task instances per repository instead of one means more scheduling overhead
(a few seconds per task on CeleryExecutor). For a daily job over tens of repositories that is
irrelevant; at thousands, the repositories would be processed in batches per mapped task.

## 11. Airflow reads GitHub itself; Django owns persistence

**Decision.** The worker calls the GitHub API directly and sends the validated result to Django's
internal API. Django never fetches metrics on Airflow's behalf, and Airflow never touches the
application database.

**Why.** Each mapped task instance then really does the work for one repository, with its own
retries, logs and timeout visible in Airflow, while the write path keeps a single owner that
enforces the idempotency rules.

**Trade-off.** There are two small GitHub clients: one in Django for validating a new repository,
one in the DAG's helper package for collecting metrics. They are different deployables with
different error handling needs, so sharing a library between the two images was judged to cost
more than about a hundred duplicated lines.

## 12. A failed repository fails the run and holds back the report

**Decision.** `trigger_daily_report` uses the `none_failed` trigger rule. If every repository
succeeds, the report is requested. If any fails, the task becomes `upstream_failed`, no report is
requested and the DAG run is marked failed. If there are no repositories, the mapped tasks are
skipped, the report task skips itself and the run succeeds.

**Why.** A report built from a partial collection would look complete. Failing the run makes the
problem visible in Airflow, while the snapshots of the healthy repositories are already stored.

**Trade-off.** One permanently broken repository (deleted on GitHub, for example) blocks reports
until it is removed from the dashboard. The alternative, a report flagged as partial, was rejected
as more states for little benefit at this scale.

## 13. Permanent errors fail fast; everything else retries with exponential backoff

**Decision.** Tasks have `retries=3`, `retry_delay=30s`, `retry_exponential_backoff=2.0` and a
two-minute `execution_timeout`. An Airflow 3.3 `ExceptionRetryPolicy` fails a task immediately when
it raises a `PermanentError` (repository gone, rejected token, invalid metrics, rate limit that
resets later than the retries can wait). Timeouts, connection errors, 5xx responses and short rate
limits fall through to the normal retries.

**Why.** Retrying a 404 three times only delays the failure by three and a half minutes. Declaring
the rule once on the DAG keeps the helper package free of Airflow imports.

**Trade-off.** Retry policies are a recent Airflow feature (3.3). The fallback, raising
`AirflowFailException` from the task wrappers, is a small change if it is ever needed.

## 14. GitHub concurrency is bounded in the DAG

**Decision.** `fetch_metrics` sets `max_active_tis_per_dagrun=4` and the DAG sets
`max_active_runs=1`, so at most four GitHub requests are in flight at any time.

**Why.** The limit lives next to the code it protects and needs no setup. An Airflow pool would
give the same bound but has to exist in the metadata database first; a task that references a
missing pool stays queued forever.

**Trade-off.** The bound is per DAG. If several DAGs called GitHub, a shared pool would be the
right tool.

## 15. Tokens reach tasks as Airflow connections defined on the worker only

**Decision.** The GitHub token and the internal API token are supplied as `AIRFLOW_CONN_*`
environment variables on the `airflow-worker` service and read inside tasks with `Connection.get`.
They are not stored in the metadata database, not set on the scheduler, DAG processor or API
server, and never returned from a task.

**Why.** Airflow masks connection passwords in task logs, nothing is resolved while the DAG file
is parsed, and XCom carries only ids, names and counts. The worker is the only component that runs
task code, so it is the only one that needs the secrets.

**Trade-off.** Environment-defined connections do not appear in the Airflow UI's connection list.

## 16. Only the Airflow components this pipeline needs

**Decision.** The stack runs the API server, scheduler, DAG processor, one Celery worker and a
one-shot init job. There is no triggerer (no deferrable tasks) and no Flower. The FAB auth manager
provides the UI login, as in Airflow's reference Compose file. The JWT secret, API secret key and
Fernet key are set explicitly and identically on every Airflow service. The DAG folder is baked
into the image and also mounted read-only, so edits show up without a rebuild.

**Why.** Every container costs memory on a laptop. Explicit shared secrets avoid the most common
Airflow 3 Compose failure, where each container generates its own JWT secret and the worker's
calls to the API server are rejected.

**Trade-off.** Adding a deferrable operator later means adding the triggerer service. Moving to
Airflow 3.4 also means switching the database URLs to the `psycopg` (version 3) driver that
release adopts as its default.

## 17. Two Celery systems on one Redis, isolated three ways

**Decision.** Airflow's CeleryExecutor and Django's Celery application share the Redis container
and nothing else. Airflow uses Redis database 0 and the queue `airflow_tasks`; Django uses database
1 and the queue `django_reports`. Each has its own worker container that subscribes to its own
queue only. Both run the same Celery, kombu and redis-py versions, taken from Airflow's constraints
file.

**Why.** Kombu keeps queues and bookkeeping per Redis database, so separate databases already make
it impossible for one worker to see the other's messages. Distinct queue names and separate task
registries are a second and third barrier, and make the separation obvious when inspecting Redis.

**Trade-off.** One Redis process is a shared point of failure for both. In production they would
normally be separate brokers, or at least separately sized.

## 18. A report's state lives in its database row, and requests are idempotent

**Decision.** There is one `DailyReport` row per snapshot date. The request endpoint chooses the
Celery task id, stores it with the row as `queued`, commits, and only then publishes the message.
A repeated request does nothing while the report is queued or running, and nothing when the same
DAG run asks again for a completed report; a different run regenerates it; failed or stale reports
are queued again. The worker only starts a report whose stored task id matches its own. If
publishing fails, the row is marked failed and the API answers 503. Django's Celery has no result
backend.

**Why.** The row is what the dashboard reads, so it must never claim more than is true: `queued`
always has a message behind it, `running` belongs to one identifiable task, and duplicate or stale
deliveries are recognised and dropped. A result backend would be a second, weaker copy of the same
information.

**Trade-off.** A report whose message is lost (Redis restarted) stays `queued` until the staleness
window of 15 minutes has passed and someone requests it again.

## 19. The DAG does not wait for the report

**Decision.** `trigger_daily_report` succeeds when Django answers 202 Accepted. It does not poll
for completion.

**Why.** It keeps the two responsibilities distinct: a successful DAG run means "metrics collected
and report requested", and the report's own lifecycle is shown in the dashboard and at
`/api/reports/`. A worker slot is not held while another system works.

**Trade-off.** A report that fails does not turn the DAG run red. If that coupling were wanted, the
bounded way to add it is a sensor in reschedule mode with a timeout, polling an internal status
endpoint.

## 20. No pipeline-run table in Django

**Decision.** `/api/pipeline/status/` is derived from stored data: the newest collection time, how
many active repositories have a snapshot for the newest date, and the latest report. Django keeps
no copy of Airflow's DAG run state.

**Why.** Airflow is the source of truth for run state and cannot reliably report its own failures
to another system (a run that cannot reach Django cannot tell Django it failed). Deriving the status
from data needs no extra writes and cannot drift.

**Trade-off.** The dashboard shows what was collected, not why a run failed; for that it links to
the Airflow UI.

## 21. The dashboard is served from the API's origin; CORS is an allowlist for development

**Decision.** nginx serves the built Vue application and proxies `/api/` to Django, so the browser
talks to a single origin. nginx answers 404 for `/api/internal/`. Django's CORS configuration is an
explicit allowlist (`CORS_ALLOWED_ORIGINS`) that by default contains only the Vite dev server's
origin, which is the one case where the dashboard calls the API cross-origin.

**Why.** Same-origin removes a whole class of "works on localhost but not on 127.0.0.1" problems
and gives the internal endpoints a second barrier besides their token: they are not routed from
the origin a browser uses. The allowlist keeps cross-origin access closed unless it is named.

**Trade-off.** There are two ways to reach the API (through port 3000 and directly on port 8000),
which the documentation has to explain. Django's port stays published on the loopback interface
because it is useful for `curl` and for the dev server.

## 22. Plain Vue 3: no router, no store, no UI framework

**Decision.** The dashboard is one page built from single-file components with the Composition
API, plain JavaScript and one stylesheet of CSS custom properties. Server state lives in one
composable (`useDashboard`), and one module (`api/client.js`) talks to the backend and turns
every failure into an `ApiError` with the API's stable error code.

**Why.** One page with one owner of the data does not need a router or a state library, and the
few visual elements do not justify a component framework. Fewer dependencies also mean a smaller
bundle and less to keep up to date.

**Trade-off.** No static typing. If the dashboard grew beyond one page, TypeScript and a router
would be the first additions.

## 23. One metric per chart, a single series each

**Decision.** History is shown as three small line charts stacked above each other (stars, forks,
open issues) that share one time range control and identical x positions. Each has one series, one
y-axis fitted to its data, the latest value written at the end of the line, a crosshair tooltip,
and a table with the same numbers. Chart.js draws them; only the line-chart pieces are registered.

**Why.** The three metrics differ by orders of magnitude, so one plot would need several y-scales,
and the alignment of independent scales suggests relationships that are not in the data. A table
keeps every value reachable without hovering and without seeing colour.

**Trade-off.** The y-axes do not start at zero (daily changes would be invisible otherwise), so the
lines show change, not proportion; the axis labels and the exact figures say how large it is.
