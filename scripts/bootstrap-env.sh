#!/usr/bin/env sh
# Creates `.env` from `.env.example` and fills every empty secret with a random value.
# Safe to re-run: an existing `.env` is never overwritten.
set -eu

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env already exists - leaving it untouched."
  exit 0
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required to generate secrets. Install it, or copy .env.example to .env and fill the empty values by hand." >&2
  exit 1
fi

random_hex() {
  openssl rand -hex "$1"
}

# A Fernet key is 32 random bytes in URL-safe base64.
fernet_key() {
  openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n'
}

# fill KEY VALUE: sets KEY only where the template leaves it empty.
fill() {
  awk -v key="$1" -v value="$2" '
    index($0, key "=") == 1 && $0 == key "=" { print key "=" value; next }
    { print }
  ' .env > .env.tmp
  mv .env.tmp .env
}

umask 077
cp .env.example .env

fill POSTGRES_SUPERUSER_PASSWORD "$(random_hex 24)"
fill ANALYTICS_DB_PASSWORD "$(random_hex 24)"
fill AIRFLOW_DB_PASSWORD "$(random_hex 24)"
fill DJANGO_SECRET_KEY "$(random_hex 32)"
fill INTERNAL_API_TOKEN "$(random_hex 32)"
fill AIRFLOW_ADMIN_PASSWORD "$(random_hex 12)"
fill AIRFLOW_FERNET_KEY "$(fernet_key)"
fill AIRFLOW_JWT_SECRET "$(random_hex 32)"
fill AIRFLOW_API_SECRET_KEY "$(random_hex 32)"

chmod 600 .env

echo "Created .env with freshly generated secrets."
echo "Airflow UI login: $(grep '^AIRFLOW_ADMIN_USERNAME=' .env | cut -d= -f2) / $(grep '^AIRFLOW_ADMIN_PASSWORD=' .env | cut -d= -f2)"
echo "Optional: set GITHUB_TOKEN in .env to raise the GitHub rate limit."
