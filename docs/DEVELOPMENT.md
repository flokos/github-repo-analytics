# Development

Everything runs in containers: no Python virtual environment and no local Node.js are needed.
The only tools required on the host are Docker with Compose v2, a POSIX shell with `openssl` (for
the bootstrap script) and, optionally, `make`.

## Project structure

```
.
├── compose.yaml               All services, their wiring and health checks
├── .env.example               Template; scripts/bootstrap-env.sh turns it into .env
├── Makefile                   Shortcuts for the commands below
├── scripts/bootstrap-env.sh   Creates .env with generated secrets
├── docker/postgres/           Init script: two databases, two roles
├── backend/                   Django project
│   ├── Dockerfile             Stages: base, test, runtime
│   ├── requirements.in        Direct dependencies; requirements.txt is the pinned result
│   ├── config/                Settings, URLs, Celery application
│   └── analytics/             The app: domain, application, infrastructure, api, tests
├── airflow/
│   ├── Dockerfile             Official Airflow image plus the DAGs; a test stage adds pytest
│   ├── dags/
│   │   ├── github_metrics_daily.py   The DAG
│   │   └── github_analytics/         Task logic as plain Python
│   └── tests/
├── frontend/                  Vue 3 + Vite
│   ├── Dockerfile             Stages: deps, dev, build, runtime (nginx)
│   ├── nginx.conf
│   ├── src/                   App.vue, components, composables, api client, utils
│   └── tests/
└── docs/
```

How the Django app is layered is explained in
[ARCHITECTURE.md](ARCHITECTURE.md#django-application-structure).

## Everyday commands

| Task | `make` | Plain command |
|---|---|---|
| Create `.env` | `make env` | `./scripts/bootstrap-env.sh` |
| Build and start everything | `make up` | `docker compose up --build --detach` |
| Status and health | `make ps` | `docker compose ps` |
| Follow all logs | `make logs` | `docker compose logs --follow --tail 100` |
| Start a pipeline run now | `make trigger` | `docker compose exec airflow-scheduler airflow dags trigger github_metrics_daily` |
| Run every test suite | `make test` | the three commands under [Tests](#tests) |
| Stop, keep data | `make down` | `docker compose down` |
| Stop and delete all data | `make clean` | `docker compose down --volumes --remove-orphans` |

Logs of one service: `docker compose logs --follow api` (or `django-worker`, `airflow-worker`,
`airflow-scheduler`, ...).

## Changing code

Application code is baked into the images, so a change takes effect after a rebuild of the
affected service. Layer caching keeps this to a few seconds.

| You changed | Run |
|---|---|
| Backend (`backend/`) | `docker compose up --build --detach api django-worker` |
| Dashboard (`frontend/`) | `docker compose up --build --detach frontend` |
| DAG or its helper package (`airflow/dags/`) | Nothing: the folder is mounted read-only into the Airflow containers. The DAG processor notices a changed file within about 30 seconds and workers load it for the next task |
| `.env` | `docker compose up --detach` (recreates the services whose configuration changed) |
| `compose.yaml` | `docker compose up --detach` |

### Dashboard with hot reload

```bash
make dev-frontend        # docker compose --profile dev up --build frontend-dev
```

This starts the Vite dev server on <http://localhost:5173> with `frontend/src` mounted, next to the
normal stack. Unlike the nginx build, it calls Django directly on port 8000, so the requests are
cross-origin; they are allowed because `http://localhost:5173` is in `CORS_ALLOWED_ORIGINS`.

### A note on commands that write files

A few commands below write into the working tree from inside a container (new migrations, lock
files, auto-formatting). They mount the directory with `-v`.

- With standard Docker, run the container as yourself so that new files belong to you instead of
  `root`: `--user "$(id -u):$(id -g)"`, or `--user "$(id -u):0"` for the Airflow image, which
  expects group 0.
- With rootless Docker, use `--user root` instead: the container's root already is your user.

The examples show the first form.

## Migrations

Django migrations are applied automatically: the one-shot `backend-migrate` service runs
`manage.py migrate` before `api` and `django-worker` start. Airflow's own database is migrated the
same way by `airflow-init`.

```bash
# Apply migrations by hand
docker compose run --rm backend-migrate

# Show which migrations are applied
docker compose run --rm backend-migrate python manage.py showmigrations analytics

# Create a migration after changing models.py
docker compose --profile test run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/backend:/app" backend-tests python manage.py makemigrations analytics
```

The backend test run fails if a model change has no migration
(`makemigrations --check --dry-run`).

## Tests

Each suite runs in its own container. `--build` makes sure the image contains your latest code.

```bash
# Django: ruff, migration check, then pytest against a throwaway PostgreSQL database
docker compose --profile test run --rm --build backend-tests

# Airflow: ruff, then pytest (unit tests and whole DAG runs on a throwaway SQLite database)
docker compose --profile test run --rm --build airflow-tests

# Vue: ESLint, then Vitest
docker compose --profile test run --rm --build frontend-tests
```

What each suite covers:

| Suite | Covers | External systems |
|---|---|---|
| Backend (`backend/analytics/tests/`) | URL parsing, the GitHub client, every REST endpoint, soft delete and reactivation, snapshot idempotency, report calculations, report requests, the Celery task with retries and status transitions | GitHub is mocked at the HTTP level with `responses`; the database is real PostgreSQL; Celery tasks run in-process |
| Airflow (`airflow/tests/`) | Both HTTP clients, metric validation, the pipeline functions, DAG import, schedule, dependencies, mapping and retry policy, and whole runs through `dag.test()` (success, one repository, no repositories, failures) | GitHub and Django are mocked at the HTTP level; no scheduler, broker or network |
| Frontend (`frontend/tests/`) | The API client, formatting and error messages, every component, and the add, remove, restore and failure flows through the whole page | The API module is mocked; charts are replaced by a stub because jsdom has no canvas |

No test contacts the real GitHub: the HTTP mocks reject any request that was not registered.

Running part of a suite:

```bash
docker compose --profile test run --rm backend-tests pytest analytics/tests/test_repositories_api.py -k duplicate
docker compose --profile test run --rm airflow-tests "pytest tests/test_validation.py"
docker compose --profile test run --rm frontend-tests npx vitest run tests/RepositoryForm.test.js
```

The Airflow tests that execute whole DAG runs (`tests/test_dag_runs.py`) need a migrated metadata
database, which the default command prepares:

```bash
docker compose --profile test run --rm airflow-tests \
  "airflow db migrate > /dev/null 2>&1 && pytest tests/test_dag_runs.py"
```

## Linting and formatting

| Code | Tool | Configuration |
|---|---|---|
| `backend/` | ruff (lint and format) | `backend/pyproject.toml` |
| `airflow/` | ruff (lint and format) | `airflow/pyproject.toml` |
| `frontend/` | ESLint with `eslint-plugin-vue` | `frontend/eslint.config.js` |

The test commands above run the linters first and fail on any finding. To fix what can be fixed
automatically:

```bash
docker compose --profile test run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/backend:/app" backend-tests \
  sh -c "ruff check --fix --no-cache . && ruff format --no-cache ."

docker compose --profile test run --rm --user "$(id -u):0" \
  -v "$PWD/airflow/dags:/opt/airflow/dags" -v "$PWD/airflow/tests:/opt/airflow/tests" \
  airflow-tests "ruff check --fix --no-cache dags tests && ruff format --no-cache dags tests"

docker compose --profile test run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/frontend/src:/app/src" -v "$PWD/frontend/tests:/app/tests" \
  frontend-tests ./node_modules/.bin/eslint . --fix
```

## Dependencies

All versions are pinned. The reasoning for the chosen versions is in
[DECISIONS.md](DECISIONS.md#1-version-policy-newest-release-line-that-already-has-patch-releases).

| Part | Edit | Then regenerate |
|---|---|---|
| Backend | `backend/requirements.in`, `backend/requirements-dev.in` | `requirements.txt` and `requirements-dev.txt` (below) |
| Airflow | `AIRFLOW_VERSION` in `airflow/Dockerfile`; test tools in `airflow/requirements-dev.txt` | nothing: the official image carries Airflow's tested dependency set |
| Frontend | `frontend/package.json` | `package-lock.json` (below) |

```bash
# Backend lock files
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/backend:/work" -w /work \
  ghcr.io/astral-sh/uv:0.9.5-python3.13-bookworm-slim \
  sh -c "uv pip compile requirements.in -o requirements.txt --python-version 3.13 && \
         uv pip compile requirements-dev.in -o requirements-dev.txt --python-version 3.13"

# Frontend lock file
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/frontend:/app" -w /app \
  node:24-alpine npm install --package-lock-only
```

The Celery packages in `backend/requirements.in` are kept at the versions in Airflow's constraints
file for the pinned Airflow release, so that both Celery systems run the same code. When
`AIRFLOW_VERSION` changes, update them from
`https://raw.githubusercontent.com/apache/airflow/constraints-<version>/constraints-3.13.txt`.

## Useful shells

```bash
docker compose exec api python manage.py shell                 # Django shell
docker compose exec postgres psql -U postgres analytics        # application database
docker compose exec postgres psql -U postgres airflow          # Airflow metadata
docker compose exec redis redis-cli -n 1                       # Django's broker database
docker compose exec airflow-scheduler airflow dags list        # Airflow CLI
```

`airflow connections get <id>` on the worker prints a connection **including its token in plain
text**, so keep it out of screen shares and logs.

## Adding a feature: an example

Suppose GitHub's `subscribers_count` ("watchers") should be collected as a fourth metric. The
change touches each layer once, in the direction the data flows:

1. **Model and migration.** Add `watchers` to `RepositorySnapshot` in `backend/analytics/models.py`
   (nullable, because existing rows have no value) and create the migration.
2. **Write path.** Add the field to `SnapshotInput` and `record_snapshot` in
   `application/snapshots.py` and to `SnapshotWriteSerializer` in `api/serializers.py`.
3. **Read path.** Add it to `SnapshotSerializer`, to the `latest_*` annotations in
   `application/queries.py`, and to `LatestSnapshotSerializer`.
4. **Pipeline.** Read `subscribers_count` in `fetch_metrics` (`airflow/dags/github_analytics/pipeline.py`),
   add it to `COUNT_FIELDS` in `validation.py` and to `SNAPSHOT_FIELDS` in `pipeline.py`. The DAG
   file itself does not change.
5. **Report.** Add it to `Metrics` and the calculations in `domain/reports.py` if it belongs in the
   daily report.
6. **Dashboard.** Add one entry to `METRICS` in `frontend/src/components/RepositoryHistory.vue` for
   a fourth chart, and a line to `RepositoryCard.vue`.
7. **Tests and documentation.** Extend the tests next to each change and the examples in
   [API.md](API.md).

Deploy order matters for a change like this: Django first (it must accept the new field), then
the DAG.

## Conventions

- Use cases raise the named errors in `analytics/domain/errors.py`; only
  `api/exception_handler.py` chooses HTTP statuses.
- Nothing in `analytics/domain/` imports Django, DRF, `requests` or Celery.
- Only `analytics/infrastructure/github_client.py` (backend) and
  `github_analytics/clients.py` (pipeline) perform outbound HTTP, always with a timeout.
- Nothing in `airflow/dags/github_analytics/` imports Airflow; the DAG file only wires those
  functions into tasks.
- Task return values are small and never contain a token: they are stored in XCom.
- Only `frontend/src/api/client.js` calls `fetch`.
- Commit messages follow the Conventional Commits style (`feat(scope): ...`, `fix: ...`, `docs: ...`).
