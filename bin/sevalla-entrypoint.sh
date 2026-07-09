#!/bin/sh
# The YAML that lms/envs/production.py reads is generated from the environment
# on every boot, so rotating a database password is a restart rather than a
# rebuild. Runs before the app so uwsgi never sees a stale config.
set -e
python /openedx/bin/render_config.py

# Off by default: migrations are a fact about the database, not about a
# container, and this script runs once per container. migrate_once.py takes a
# MySQL advisory lock so concurrent boots serialise rather than race, but it
# still adds tens of seconds to every start. Turn it on to bootstrap a fresh
# database, then unset it.
if [ "${RUN_MIGRATIONS_ON_BOOT:-false}" = "true" ]; then
    python /openedx/bin/migrate_once.py
fi

exec "$@"
