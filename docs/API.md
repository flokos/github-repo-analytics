# REST API

The Django application exposes two groups of endpoints under `/api/`:

- **Public endpoints**, used by the dashboard. They need no authentication (see
  [Security limits](ARCHITECTURE.md#security-limits-of-the-local-setup)).
- **Internal endpoints** under `/api/internal/`, used by the Airflow pipeline. They require a
  bearer token and are not routed by nginx.

Base URLs in the Compose stack:

| From | Base URL | Notes |
|---|---|---|
| Browser, through nginx | `http://localhost:3000/api/` | `/api/internal/` answers 404 here |
| Host, directly | `http://localhost:8000/api/` | For `curl` and the Vite dev server |
| Airflow containers | `http://api:8000/api/` | Compose network |

Requests and responses are JSON (`Content-Type: application/json`). Timestamps are ISO 8601 in
UTC; dates are `YYYY-MM-DD` and always mean a UTC calendar day.

## Errors

Every error, from any endpoint, has the same shape:

```json
{
  "error": {
    "code": "duplicate_repository",
    "message": "This repository is already being tracked.",
    "details": {}
  }
}
```

`code` is stable and meant for programs; `message` is meant for people; `details` carries extra
data for some codes.

| HTTP | `code` | When | `details` |
|---|---|---|---|
| 400 | `invalid_repository_url` | The URL is not `https://github.com/<owner>/<name>` | |
| 400 | `validation_error` | A field is missing or has the wrong type or range | `fields`: messages per field |
| 400 | `parse_error` | The body is not valid JSON | |
| 401 | `not_authenticated` | Internal endpoint called without a bearer token | |
| 401 | `authentication_failed` | Internal endpoint called with a wrong or malformed token | |
| 404 | `not_found` | Unknown repository id or unknown path | |
| 405 | `method_not_allowed` | | |
| 409 | `duplicate_repository` | The repository is already tracked and active | |
| 415 | `unsupported_media_type` | The body is not sent as JSON | |
| 422 | `repository_unavailable` | GitHub answered 404: the repository does not exist **or** is private | |
| 422 | `repository_not_public` | GitHub returned the repository, but it is not public | |
| 422 | `repository_access_denied` | GitHub answered 403 or 451 for this repository | |
| 422 | `no_snapshots_for_date` | A report was requested for a date without snapshots | |
| 429 | `throttled` | Too many `POST /api/repositories/` calls from one client | `retry_after` (seconds) |
| 502 | `github_unavailable` | GitHub answered 5xx, was unreachable, or sent an unreadable response | |
| 503 | `github_rate_limited` | GitHub's rate limit is exhausted; also sets `Retry-After` | `retry_after` (seconds), when known |
| 503 | `report_dispatch_failed` | The report could not be queued (broker unreachable) | |
| 504 | `github_timeout` | GitHub did not answer in time | |
| 500 | `internal_error` | Anything unexpected | |

4xx means the request cannot succeed as sent. 5xx means the same request may succeed later.

A GitHub 404 is reported as `repository_unavailable` with a message that deliberately does not
claim the repository is missing: GitHub gives the same answer for private repositories.

## Public endpoints

### `GET /api/repositories/`

Tracked repositories with their latest metrics, ordered by owner and name.

| Query parameter | Values | Default |
|---|---|---|
| `status` | `active`, `inactive` (removed), `all` | `active` |

```bash
curl http://localhost:8000/api/repositories/
```

```json
[
  {
    "id": 1,
    "github_id": 4164482,
    "owner": "django",
    "name": "django",
    "full_name": "django/django",
    "html_url": "https://github.com/django/django",
    "is_active": true,
    "created_at": "2026-11-10T11:00:55.336840Z",
    "deactivated_at": null,
    "last_synced_at": "2026-11-10T12:16:02.412345Z",
    "latest_snapshot": {
      "snapshot_date": "2026-11-10",
      "stars": 91699,
      "forks": 35213,
      "open_issues": 534,
      "collected_at": "2026-11-10T12:16:02.412345Z"
    }
  }
]
```

`last_synced_at` is the time of the last successful collection. It and `latest_snapshot` are
`null` for a repository that has not been collected yet. `open_issues` is GitHub's
`open_issues_count`, which **includes open pull requests**.

Errors: 400 `validation_error` for an unknown `status`.

### `POST /api/repositories/`

Start tracking a repository. The backend validates the URL, asks GitHub whether the repository
exists and is public, and only then stores it.

```bash
curl -X POST http://localhost:8000/api/repositories/ \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://github.com/django/django"}'
```

Accepted URL forms: `https://github.com/<owner>/<name>`, optionally with `www.`, a trailing slash
or a `.git` suffix. Anything else is rejected without contacting GitHub: other hosts, `http://`,
credentials, ports, query strings, fragments, and deeper paths such as `/tree/main`.

| Result | Status | Body |
|---|---|---|
| New repository stored | **201** | The repository (as in the list) plus `"reactivated": false` |
| A removed repository brought back | **200** | The repository, same `id` and history, plus `"reactivated": true` |

The stored `owner` and `name` are the canonical ones GitHub returns, not the casing that was typed.

Errors: 400 `invalid_repository_url`, 400 `validation_error` (no `url`), 409
`duplicate_repository`, 422 `repository_unavailable` / `repository_not_public` /
`repository_access_denied`, 429 `throttled`, 502 `github_unavailable`, 503
`github_rate_limited`, 504 `github_timeout`.

```json
{
  "error": {
    "code": "repository_unavailable",
    "message": "GitHub reports this repository as unavailable. It may not exist, or it may be private.",
    "details": {}
  }
}
```

This endpoint is throttled per client (30 requests per minute by default,
`REPOSITORY_CREATE_THROTTLE`), because every call triggers one outbound GitHub request.

### `DELETE /api/repositories/{id}/`

Remove a repository from active tracking. This is a soft delete: the row and all snapshots stay,
and the repository can be reactivated with `POST /api/repositories/`.

```bash
curl -X DELETE http://localhost:8000/api/repositories/1/
```

Status **204**, no body. Repeating the call also answers 204. Errors: 404 `not_found`.

### `GET /api/repositories/{id}/snapshots/`

Daily snapshots of one repository, oldest first. Works for removed repositories too.

| Query parameter | Values | Default |
|---|---|---|
| `days` | 1 to 366: how many days back, counting today | 90 |

```bash
curl 'http://localhost:8000/api/repositories/1/snapshots/?days=30'
```

```json
[
  {
    "snapshot_date": "2026-11-10",
    "stars": 91699,
    "forks": 35213,
    "open_issues": 534,
    "collected_at": "2026-11-10T12:16:02.412345Z"
  }
]
```

Errors: 400 `validation_error` (`days` out of range), 404 `not_found`.

### `GET /api/dashboard/summary/`

Totals over the latest snapshot of every active repository.

```json
{
  "active_repositories": 3,
  "repositories_with_data": 3,
  "total_stars": 194028,
  "total_forks": 60731,
  "total_open_issues": 2241,
  "last_synced_at": "2026-11-10T12:16:02.412345Z"
}
```

`repositories_with_data` is smaller than `active_repositories` while newly added repositories
wait for their first collection.

### `GET /api/reports/`

Daily reports, newest first.

| Query parameter | Values | Default |
|---|---|---|
| `limit` | 1 to 100 | 30 |

```json
[
  {
    "id": 1,
    "snapshot_date": "2026-11-10",
    "status": "completed",
    "requested_at": "2026-11-10T12:16:04.101112Z",
    "started_at": "2026-11-10T12:16:04.160000Z",
    "finished_at": "2026-11-10T12:16:04.180000Z",
    "attempts": 1,
    "error_message": "",
    "repository_count": 3,
    "total_stars": 194028,
    "total_forks": 60731,
    "total_open_issues": 2241,
    "stars_delta": 57,
    "forks_delta": 4,
    "open_issues_delta": -3,
    "repository_changes": [
      {
        "repository_id": 1,
        "full_name": "django/django",
        "stars": 91699,
        "forks": 35213,
        "open_issues": 534,
        "stars_delta": 42,
        "forks_delta": 3,
        "open_issues_delta": -2,
        "previous_snapshot_date": "2026-11-09"
      }
    ]
  }
]
```

- `status` is `queued`, `running`, `completed` or `failed`. It describes the Django Celery job,
  not the Airflow DAG run.
- A change (`*_delta`) is the difference to that repository's previous available snapshot. It is
  `null` in `repository_changes` for a repository collected for the first time, and such a
  repository does not contribute to the aggregate deltas.
- `repository_changes` is ordered by stars gained, largest first.
- A report that was requested again keeps its previous results until the new generation
  completes.

### `GET /api/pipeline/status/`

What the stored data says about the pipeline: the latest collection and the latest report.

```json
{
  "collection": {
    "last_collected_at": "2026-11-10T12:16:02.412345Z",
    "latest_snapshot_date": "2026-11-10",
    "repositories_collected": 3,
    "active_repositories": 3
  },
  "latest_report": {
    "id": 1,
    "snapshot_date": "2026-11-10",
    "status": "completed"
  }
}
```

`latest_report` has all the fields of an item of `GET /api/reports/` (shortened here), or is
`null` when no report exists. `repositories_collected` counts the active repositories that have
a snapshot for `latest_snapshot_date`. The state of Airflow DAG runs is not part of this
response; it lives in Airflow.

### `GET /api/health/`

`{"status": "ok"}` when Django can reach its database. Used by the container health check.

## Internal endpoints

Used only by the Airflow DAG. Every request must carry the shared secret from `.env`:

```
Authorization: Bearer <INTERNAL_API_TOKEN>
```

Without the header the answer is 401 `not_authenticated`; with a wrong token, 401
`authentication_failed`. Through nginx (port 3000) these paths answer 404 regardless of the token.

```bash
TOKEN=$(grep '^INTERNAL_API_TOKEN=' .env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/internal/repositories/active/
```

### `GET /api/internal/repositories/active/`

The work list for one pipeline run. Deliberately small, because it travels through Airflow XCom.

```json
[
  { "id": 1, "github_id": 4164482, "owner": "django", "name": "django", "full_name": "django/django" }
]
```

### `POST /api/internal/snapshots/`

Store one repository's metrics.

```json
{
  "repository_id": 1,
  "stars": 91699,
  "forks": 35213,
  "open_issues": 534,
  "collected_at": "2026-11-10T12:16:02.412345+00:00"
}
```

The server derives `snapshot_date` from `collected_at` (its UTC date); the caller cannot choose
it. The call is idempotent per repository and day:

| Situation | Status | `outcome` |
|---|---|---|
| First snapshot of that repository for that day | **201** | `created` |
| A later observation on the same day | **200** | `updated` |
| The same or an older observation (a retry) | **200** | `unchanged` |

```json
{ "id": 12, "repository_id": 1, "snapshot_date": "2026-11-10", "outcome": "created" }
```

Snapshots are accepted for repositories that have been removed in the meantime, so that removing
a repository while a run is in flight does not fail the run.

Errors: 400 `validation_error` (negative or non-integer counts, values above 2,147,483,647, a
missing field, an unreadable or future `collected_at`), 404 `not_found` (unknown
`repository_id`).

### `POST /api/internal/reports/`

Ask for the daily report of one snapshot date to be generated.

```json
{ "snapshot_date": "2026-11-10", "dag_run_id": "scheduled__2026-11-10T06:00:00+00:00" }
```

`dag_run_id` is optional. Status **202 Accepted**: the work is queued for the Django Celery
worker, not done.

```json
{ "id": 1, "snapshot_date": "2026-11-10", "status": "queued", "enqueued": true }
```

The request is idempotent. What a repeated request does depends on the report's state:

| State of the report for that date | Effect | `enqueued` |
|---|---|---|
| None yet | Created as `queued` and queued | `true` |
| `queued` or `running` (for less than 15 minutes) | Nothing: work is under way | `false` |
| `completed`, requested by the same `dag_run_id` | Nothing: a retry of a request that worked | `false` |
| `completed`, requested by another run | Queued again (newer snapshots) | `true` |
| `failed` | Queued again | `true` |
| `queued` or `running` for 15 minutes or more | Considered stuck and queued again | `true` |

Errors: 400 `validation_error`, 422 `no_snapshots_for_date`, 503 `report_dispatch_failed` (the
report is then marked `failed`, so the next request queues it again).
