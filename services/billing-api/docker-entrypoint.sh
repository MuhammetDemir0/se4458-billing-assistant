#!/bin/sh
set -e

# "seed [--reset]" runs the one-off demo data loader (used by an ECS run-task); anything else starts the API.
if [ "$1" = "seed" ]; then
  shift
  alembic upgrade head
  exec python -m app.seed "$@"
fi

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
