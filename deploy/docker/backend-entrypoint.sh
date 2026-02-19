#!/usr/bin/env sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data
  chown -R app:app /app/data
  exec gosu app "$@"
fi

exec "$@"
