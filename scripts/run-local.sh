#!/usr/bin/env bash
# Run the bot locally with zero external infra: portable Redis + a SQLite DB +
# the FastAPI app, all in one terminal. Replies are generated in-request
# (WEBHOOK_CONVERSATION_DISPATCH=inline) so no Celery worker/beat is needed.
#
#   bash scripts/run-local.sh
#
# Ctrl+C stops the app AND the Redis it started. For the full Postgres + Celery
# stack see docs/RUNNING.md.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "!! no venv — run:  $ROOT/.venv ... python -m pip install -e '.[dev]'"; exit 1; }

REDIS_DIR="$ROOT/.local/redis"
REDIS_SRV="$REDIS_DIR/redis-server.exe"
REDIS_CLI="$REDIS_DIR/redis-cli.exe"
[ -x "$REDIS_SRV" ] || REDIS_SRV="$(command -v redis-server || true)"
[ -x "$REDIS_CLI" ] || REDIS_CLI="$(command -v redis-cli || true)"

# --- .env sanity ----------------------------------------------------------
[ -f .env ] || { echo "!! no .env — cp .env.example .env  and add your keys"; exit 1; }
if ! grep -q '^WEBHOOK_CONVERSATION_DISPATCH=inline' .env; then
  echo "note: .env is not in 'inline' mode; this script does not start a Celery worker."
  echo "      set WEBHOOK_CONVERSATION_DISPATCH=inline for the no-Celery path, or use docs/RUNNING.md."
fi

REDIS_STARTED=0
cleanup() {
  echo
  [ "$REDIS_STARTED" = 1 ] && { echo "stopping redis..."; kill "${REDIS_PID:-0}" 2>/dev/null || true; }
}
trap cleanup EXIT INT TERM

# --- redis --------------------------------------------------------------
if "$REDIS_CLI" -p 6379 ping >/dev/null 2>&1; then
  echo "redis  : already running on :6379"
else
  [ -x "$REDIS_SRV" ] || { echo "!! no redis-server. Re-run this once with internet to fetch the portable build, or install one."; exit 1; }
  echo "redis  : starting portable redis on :6379"
  "$REDIS_SRV" --port 6379 --bind 127.0.0.1 --save '' --appendonly no >/dev/null 2>&1 &
  REDIS_PID=$!
  REDIS_STARTED=1
  for _ in $(seq 1 20); do "$REDIS_CLI" -p 6379 ping >/dev/null 2>&1 && break; sleep 0.3; done
  "$REDIS_CLI" -p 6379 ping >/dev/null 2>&1 || { echo "!! redis did not come up"; exit 1; }
  echo "redis  : PONG"
fi

# --- schema ------------------------------------------------------------
echo "db     : applying migrations"
"$PY" -m alembic upgrade head >/dev/null
echo "db     : $("$PY" -m alembic current 2>/dev/null | tail -1)"

# --- config recap ----------------------------------------------------
echo "-------------------------------------------------------------------"
"$PY" -m app.cli check-config | grep -E "llm_provider|reply_model|whatsapp_client|database_url|redis_url" || true
echo "-------------------------------------------------------------------"

# --- app (foreground) ----------------------------------------------
echo "app    : http://127.0.0.1:8000   (Ctrl+C to stop everything)"
echo "         /readyz  /webhook/whatsapp"
if grep -q '^DEMO_ENABLED=true' .env 2>/dev/null; then
  echo "         /demo    (WhatsApp-lookalike demo UI, real engine, no Meta)"
fi
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
