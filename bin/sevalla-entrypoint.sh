#!/bin/sh
# The YAML that lms/envs/production.py reads is generated from the environment
# on every boot, so rotating a database password is a restart rather than a
# rebuild. Runs before the app so uwsgi never sees a stale config.
set -e
python /openedx/bin/render_config.py
exec "$@"
