#!/usr/bin/env bash
# Stop anything scripts/run-local.sh (or a manual local run) left running:
# the portable Redis and any uvicorn on :8000.
set -uo pipefail

echo "stopping uvicorn / redis-server ..."
# uvicorn (python running app.main)
taskkill //F //FI "IMAGENAME eq python.exe" //FI "WINDOWTITLE eq *uvicorn*" 2>/dev/null || true
for pid in $(netstat -ano 2>/dev/null | awk '/:8000 .*LISTENING/{print $NF}' | sort -u); do
  taskkill //F //PID "$pid" 2>/dev/null || true
done
# portable redis
taskkill //F //IM redis-server.exe 2>/dev/null || true

echo "done. (SQLite DB leadbot.db is left in place — delete it to reset all data.)"
