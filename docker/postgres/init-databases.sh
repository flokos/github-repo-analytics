#!/bin/bash
# Runs once, when the PostgreSQL data directory is first initialised.
# Creates one role and one database per application so that neither can read the other's data.
set -euo pipefail

create_role_and_database() {
  local role="$1" password="$2" database="$3" role_options="${4:-}"

  psql --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set role="$role" --set password="$password" --set database="$database" <<EOSQL
CREATE ROLE :"role" LOGIN PASSWORD :'password' ${role_options};
CREATE DATABASE :"database" OWNER :"role";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
EOSQL
  echo "Created role and database '${database}'."
}

# CREATEDB lets the Django test runner create and drop its throwaway test database.
create_role_and_database "$ANALYTICS_DB_USER" "$ANALYTICS_DB_PASSWORD" "$ANALYTICS_DB_NAME" "CREATEDB"
create_role_and_database "$AIRFLOW_DB_USER" "$AIRFLOW_DB_PASSWORD" "$AIRFLOW_DB_NAME"
