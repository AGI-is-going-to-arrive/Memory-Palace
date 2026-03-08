#!/usr/bin/env sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data /app/snapshots
  chown -R app:app /app/data /app/snapshots
  exec gosu app "$@"
fi

exec "$@"
