# Diagrams

All diagrams are Mermaid and render directly on GitHub. They describe the system as implemented;
the prose that goes with them is in [ARCHITECTURE.md](ARCHITECTURE.md), [AIRFLOW.md](AIRFLOW.md)
and [CELERY.md](CELERY.md).

## 1. System architecture

Arrows are requests, labelled with what is asked for. Arrows between the coloured groups name the
service that makes the call. The browser only ever talks to nginx and, for the Airflow UI, to the
Airflow API server.

There is **one** Redis container and **one** PostgreSQL container. Each is drawn twice, inside the
application that uses it, because each application only ever sees its own logical database on it.

```mermaid
flowchart LR
    user([Browser])
    github[(GitHub REST API)]

    subgraph dashboard [Dashboard]
        nginx["frontend<br/>nginx + built Vue app"]
    end

    subgraph django [Django]
        direction TB
        api["api<br/>Django + DRF on gunicorn"]
        redis1[("Redis db 1<br/>queue django_reports")]
        dworker["django-worker<br/>Celery worker"]
        pgapp[("PostgreSQL db analytics<br/>repositories, snapshots, reports")]
        api -->|"publish report task"| redis1
        redis1 -->|"deliver"| dworker
        api --> pgapp
        dworker --> pgapp
    end

    subgraph airflow [Airflow 3 with CeleryExecutor]
        direction TB
        scheduler[airflow-scheduler]
        dagproc[airflow-dag-processor]
        redis0[("Redis db 0<br/>queue airflow_tasks")]
        aworker["airflow-worker<br/>Celery worker"]
        apiserver["airflow-apiserver<br/>UI, REST API, execution API"]
        pgair[("PostgreSQL db airflow<br/>metadata, Celery results")]
        scheduler -->|"publish task instances"| redis0
        redis0 -->|"deliver"| aworker
        aworker -->|"task state, XCom"| apiserver
        scheduler --> pgair
        dagproc --> pgair
        apiserver --> pgair
    end

    user -->|"dashboard and /api/"| dashboard
    dashboard -->|"/api/ except /api/internal/"| django
    user -->|"Airflow UI"| airflow
    airflow -->|"airflow-worker calls /api/internal/<br/>with a bearer token"| django
    django -->|"api validates a new repository"| github
    airflow -->|"airflow-worker fetches metrics"| github
```

## 2. Repository creation and validation

The submitted URL is parsed and never fetched. Nothing is stored until GitHub has confirmed that
the repository exists and is public.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Vue as Vue dashboard
    participant API as Django API
    participant Domain as URL parser (domain)
    participant GH as GitHub REST API
    participant DB as PostgreSQL (analytics)

    User->>Vue: paste https://github.com/owner/name
    Vue->>API: POST /api/repositories/ {url}
    API->>Domain: parse_repository_url(url)
    alt not a github.com repository URL
        Domain-->>API: InvalidRepositoryUrl
        API-->>Vue: 400 invalid_repository_url
    else valid owner and name
        Domain-->>API: RepositoryRef(owner, name)
        API->>DB: already tracked and active?
        alt yes
            API-->>Vue: 409 duplicate_repository
        else no
            API->>GH: GET /repos/{owner}/{name}
            alt 200 and public
                GH-->>API: id, canonical owner and name, visibility
                API->>DB: insert, or reactivate the removed row
                API-->>Vue: 201 created, or 200 reactivated
            else 200 but not public
                API-->>Vue: 422 repository_not_public
            else 404
                API-->>Vue: 422 repository_unavailable
            else 403 or 429 with rate limit headers
                API-->>Vue: 503 github_rate_limited + Retry-After
            else timeout
                API-->>Vue: 504 github_timeout
            else 5xx or unreadable response
                API-->>Vue: 502 github_unavailable
            end
        end
    end
    Vue-->>User: confirmation, or an explanation of what went wrong
```

## 3. Airflow DAG execution

`collect_repository` is a mapped task group: its three tasks run once per repository returned by
the first task. The dashed arrows show what happens to the report task in each outcome.

```mermaid
flowchart TD
    schedule([Every day 06:00 UTC<br/>or manual trigger]) --> load

    load[load_active_repositories<br/>GET /api/internal/repositories/active/]
    load -->|"list of N repositories (XCom)"| expand{{Dynamic task mapping<br/>one group instance per repository}}

    subgraph group ["collect_repository [N instances]"]
        fetch[fetch_metrics<br/>GET api.github.com/repos/owner/name<br/>at most 4 at a time]
        validate[validate_metrics<br/>types, ranges, GitHub id, still public<br/>never retried]
        persist[persist_snapshot<br/>POST /api/internal/snapshots/<br/>idempotent per repository and day]
        fetch --> validate --> persist
    end

    expand --> fetch
    persist --> report

    report[trigger_daily_report<br/>trigger rule: none_failed<br/>POST /api/internal/reports/]

    report -.->|"all instances succeeded"| requested([Report queued in Django<br/>run: success])
    report -.->|"N = 0, mapped tasks skipped"| skipped([Report task skips itself<br/>run: success])
    report -.->|"any instance failed"| blocked([upstream_failed, no report<br/>run: failed])
```

## 4. Django Celery report generation

The request only queues work and answers 202. The report's status lives in its database row and
is independent of the Airflow run that asked for it.

```mermaid
sequenceDiagram
    autonumber
    participant Task as Airflow task<br/>trigger_daily_report
    participant API as Django API
    participant DB as PostgreSQL (analytics)
    participant Redis as Redis db 1<br/>queue django_reports
    participant Worker as django-worker
    participant Vue as Vue dashboard

    Task->>API: POST /api/internal/reports/ {snapshot_date, dag_run_id}
    API->>DB: get or create DailyReport(snapshot_date), lock the row
    alt queued or running, or completed for this same run
        API-->>Task: 202 {enqueued: false}
    else new, failed, stale, or completed for another run
        API->>DB: status = queued, store a new task id, commit
        API->>Redis: publish generate_daily_report(report_id) under that task id
        API-->>Task: 202 {enqueued: true}
    end
    Note over Task: The Airflow task is done here.<br/>Its success means "accepted", not "generated".

    Redis->>Worker: deliver the task
    Worker->>DB: start: queued to running (only if the task id matches)
    Worker->>DB: read the day's snapshots and each repository's previous one
    Worker->>DB: store totals and changes, status = completed
    Vue->>API: GET /api/pipeline/status/ (every 15 s)
    API-->>Vue: latest report and its status
```

```mermaid
stateDiagram-v2
    [*] --> queued: requested
    queued --> running: worker starts the task
    running --> completed: results stored
    running --> queued: temporary error, retry scheduled
    running --> failed: unexpected error, or retries used up
    queued --> failed: message could not be published
    completed --> queued: requested again by a later run
    failed --> queued: requested again
```

## 5. Data model relationships

`DailyReport` has no foreign key: it summarises every snapshot that shares its `snapshot_date`.

```mermaid
erDiagram
    REPOSITORY ||--o{ REPOSITORY_SNAPSHOT : "has one per day"
    DAILY_REPORT }o..o{ REPOSITORY_SNAPSHOT : "summarises by snapshot_date"

    REPOSITORY {
        bigint id PK
        bigint github_id UK "GitHub's id, survives renames"
        varchar owner "unique with name, case-insensitive"
        varchar name
        boolean is_active "false = removed (soft delete)"
        timestamptz created_at
        timestamptz updated_at
        timestamptz deactivated_at "null while active"
    }

    REPOSITORY_SNAPSHOT {
        bigint id PK
        bigint repository_id FK "unique with snapshot_date"
        date snapshot_date "UTC day of collection"
        int stars
        int forks
        int open_issues "includes open pull requests"
        timestamptz collected_at
        timestamptz created_at
        timestamptz updated_at
    }

    DAILY_REPORT {
        bigint id PK
        date snapshot_date UK
        varchar status "queued, running, completed, failed"
        timestamptz requested_at
        timestamptz started_at
        timestamptz finished_at
        varchar trigger_run_id "Airflow run that asked last"
        varchar celery_task_id "task that owns this generation"
        smallint attempts
        text error_message
        int repository_count
        bigint total_stars
        bigint total_forks
        bigint total_open_issues
        bigint stars_delta
        bigint forks_delta
        bigint open_issues_delta
        jsonb repository_changes "one line per repository"
        timestamptz created_at
        timestamptz updated_at
    }
```
