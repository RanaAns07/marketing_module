#!/bin/sh
set -e

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"

timeout=30
count=0
while ! nc -z "$POSTGRES_HOST" "$POSTGRES_PORT"; do
  sleep 1
  count=$((count+1))
  if [ "$count" -ge "$timeout" ]; then
    echo "PostgreSQL not available after ${timeout} seconds"
    exit 1
  fi
done

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
fi

exec "$@"
