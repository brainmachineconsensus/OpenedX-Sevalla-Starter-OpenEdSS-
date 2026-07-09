#!/usr/bin/env python
"""
Run the LMS and CMS migrations once, serialised by a MySQL advisory lock.

The entrypoint runs once per *container*, and a deployment has at least two of
them -- LMS and Studio -- plus however many replicas the platform scales to.
Django takes no cross-process lock, so concurrent `migrate` runs race: both read
django_migrations, both conclude the same migration is unapplied, both run its
DDL, and the loser dies on `1050 Table already exists`, possibly having left a
table created but not recorded as applied. MySQL's GET_LOCK serialises them --
whoever arrives second blocks, then finds nothing to do.

Only invoked when RUN_MIGRATIONS_ON_BOOT=true. Building edx-platform's migration
graph takes tens of seconds even when every migration is already applied, so
this is not something to leave on for every boot.
"""
import os
import subprocess
import sys

MANAGE = "/openedx/bin/openedx-manage"


class MissingSetting(Exception):
    pass


def req(name):
    value = os.environ.get(name)
    if not value:
        raise MissingSetting(name)
    return value


def lock_name(database):
    # GET_LOCK names are scoped to the server, not the schema, so two Open edX
    # databases on one MySQL instance would otherwise block each other. Names
    # are capped at 64 characters.
    return f"openedx-migrate-{database}"[:64]


def connect():
    import MySQLdb

    return MySQLdb.connect(
        host=req("MYSQL_HOST"),
        port=int(os.environ.get("MYSQL_PORT") or 3306),
        user=req("MYSQL_USERNAME"),
        password=req("MYSQL_PASSWORD"),
        database=req("MYSQL_DATABASE"),
        connect_timeout=10,
    )


def migrate(variant):
    print(f"migrate_once: running {variant} migrations", file=sys.stderr)
    subprocess.check_call([MANAGE, variant, "migrate", "--noinput"])


def main():
    try:
        # cms/envs/sevalla/production.py reads these at import time, so
        # `cms migrate` needs them even though no migration touches OAuth.
        req("CMS_OAUTH2_KEY")
        req("CMS_OAUTH2_SECRET")
        database = req("MYSQL_DATABASE")
        connection = connect()
    except MissingSetting as exc:
        sys.exit(f"error: required environment variable {exc} is not set")

    timeout = int(os.environ.get("MIGRATION_LOCK_TIMEOUT") or 900)
    name = lock_name(database)
    cursor = connection.cursor()

    # The lock lives on this connection, so it has to stay open for as long as
    # the migrations run, and is released even if they fail.
    cursor.execute("SELECT GET_LOCK(%s, %s)", (name, timeout))
    if cursor.fetchone()[0] != 1:
        sys.exit(
            f"error: could not acquire migration lock '{name}' within {timeout}s.\n"
            "       Another container is probably still migrating. Raise\n"
            "       MIGRATION_LOCK_TIMEOUT if the first run is legitimately slow."
        )

    print(f"migrate_once: holding lock '{name}'", file=sys.stderr)
    try:
        migrate("lms")
        migrate("cms")
    finally:
        cursor.execute("SELECT RELEASE_LOCK(%s)", (name,))
        cursor.close()
        connection.close()

    print("migrate_once: done", file=sys.stderr)


if __name__ == "__main__":
    main()
