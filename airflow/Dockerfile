# syntax=docker/dockerfile:1
# Bumping Airflow is a change to AIRFLOW_VERSION here (and, from 3.4 on, to the database driver in
# compose.yaml; see docs/DECISIONS.md).
ARG AIRFLOW_VERSION=3.3.2
ARG AIRFLOW_PYTHON_VERSION=3.13

# ---- base: the official image plus the DAG code --------------------------------------------------
# The official image already ships everything the pipeline imports (the Celery and FAB providers,
# requests, pendulum), so no packages are added and its tested dependency set stays untouched.
FROM apache/airflow:${AIRFLOW_VERSION}-python${AIRFLOW_PYTHON_VERSION} AS base

COPY --chown=airflow:0 dags/ /opt/airflow/dags/

# ---- test: adds pytest and ruff; used only by the `airflow-tests` Compose service ----------------
FROM base AS test

ARG AIRFLOW_VERSION
ARG AIRFLOW_PYTHON_VERSION

COPY requirements-dev.txt /tmp/requirements-dev.txt
# Airflow's constraints file keeps the extra tools from moving any of Airflow's own dependencies.
RUN pip install --no-cache-dir --requirement /tmp/requirements-dev.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${AIRFLOW_PYTHON_VERSION}.txt"

COPY --chown=airflow:0 pyproject.toml /opt/airflow/pyproject.toml
COPY --chown=airflow:0 tests/ /opt/airflow/tests/

# ---- runtime: what every Airflow service runs ----------------------------------------------------
FROM base AS runtime
