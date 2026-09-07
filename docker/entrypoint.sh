#!/usr/bin/env bash
set -euo pipefail

# Wait for the database, then apply migrations before starting whatever CMD is.
echo "[entrypoint] running database migrations (alembic upgrade head)"
for attempt in $(seq 1 30); do
  if alembic upgrade head; then
    echo "[entrypoint] migrations applied"
    break
  fi
  echo "[entrypoint] migration attempt ${attempt} failed, retrying in 2s"
  sleep 2
  if [ "${attempt}" -eq 30 ]; then
    echo "[entrypoint] giving up on migrations" >&2
    exit 1
  fi
done

exec "$@"
