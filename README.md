# OpenedX Sevalla Starter (OpenEdSS)

Runs Open edX **Ulmo** on Sevalla as two container apps (LMS and Studio) sharing
one image, with Sevalla Object Storage for uploads. Tutor is not used, at build
time or at deploy time.

MIT licensed, © 2026 Brain Machine & Consensus. The Open edX platform itself is
AGPLv3 — see [LICENSE](LICENSE).

## What is in here

| Path | Purpose |
| --- | --- |
| `Dockerfile` | Extends `overhangio/openedx:21.0.8`, adds the settings modules and entrypoint. |
| `uwsgi.ini` | Replaces the base image's, which hardcodes port 8000. Binds `$(PORT)` and picks `lms/wsgi.py` or `cms/wsgi.py` from `$(SERVICE_VARIANT)`. |
| `bin/render_config.py` | Writes `/openedx/config/{lms,cms}.env.yml` from environment variables, at every boot. |
| `bin/sevalla-entrypoint.sh` | Renders the config, then execs uwsgi. The image's `ENTRYPOINT`. |
| `bin/openedx-manage` | Wrapper for `manage.py`. Use it in Sevalla's web terminal. |
| `settings/common_all.py` | Settings shared by both variants that YAML cannot express. |
| `settings/lms_production.py` | Copied to `lms/envs/sevalla/production.py`. |
| `settings/cms_production.py` | Copied to `cms/envs/sevalla/production.py`. |

### Why config is split in two

`lms/envs/production.py` reads a YAML file named by the `LMS_CFG` environment
variable and merges its keys into Django settings. YAML has no interpolation, so
`render_config.py` generates that file from the environment on each boot —
rotating a database password is a restart, not a rebuild.

Some settings cannot be YAML at all, and live in `settings/` instead:

- `LOGGING` — the default binds `SysLogHandler` to `/dev/log`, which does not
  exist in a container, and `production.py` *assigns* `LOGGING` after the YAML
  merge, so a YAML key would be discarded.
- `codejail.jail_code.configure()` and `update_module_store_settings()` are
  function calls.
- `INSTALLED_APPS.remove(...)` is a list mutation.
- The JWT signing keys are derived from a PEM.

## Build

The image publishes amd64 and arm64; Sevalla runs amd64.

```sh
docker build --platform linux/amd64 -t your-registry/openedx:ulmo .
```

## Generate the two secrets

```sh
openssl rand -hex 32          # -> OPENEDX_SECRET_KEY
openssl genrsa 2048           # -> JWT_RSA_PRIVATE_KEY (paste the whole PEM)
```

Hex rather than base64 for the secret key: any high-entropy value works, but `/`
and `+` are awkward to move through shells and dashboard fields.

Set both as environment variables on **each app**, with identical values.
`render_config.py` reads them from the environment at every boot, so they are
runtime variables, not build arguments — rotating one is a restart, not a
rebuild. Neither has a default: a missing or empty value aborts the container at
boot with a named error.

`OPENEDX_SECRET_KEY` becomes Django's `SECRET_KEY`, which `common_all.py` then
reuses as `JWT_AUTH['JWT_SECRET_KEY']`; the signing JWKs are derived from
`JWT_RSA_PRIVATE_KEY`. The LMS signs tokens and Studio verifies them, so if the
two apps disagree on either value, Studio's OAuth2 login fails at verification.

Store both in a password manager or secret store, not only in the Sevalla
dashboard. Rotating `OPENEDX_SECRET_KEY` invalidates every session and every
outstanding password-reset link; rotating `JWT_RSA_PRIVATE_KEY` invalidates every
issued token. Nothing is lost — users log in again — but change them on both apps
in the same maintenance window, or Studio stays broken until the values match.

## Deploy as two Sevalla apps

Both use the same image. They differ only in these variables:

| | LMS app | Studio app |
| --- | --- | --- |
| `SERVICE_VARIANT` | `lms` (image default) | `cms` |
| `DJANGO_SETTINGS_MODULE` | `lms.envs.sevalla.production` (default) | `cms.envs.sevalla.production` |

Sevalla sets `PORT` itself.

### Connect the datastores to both apps

In Sevalla, a managed MySQL, Redis or Object Storage resource has to be
explicitly connected to an application before that application can reach it on
the internal network. **Do this for the LMS app and the Studio app.** Both talk to
MySQL, Redis and Mongo directly; Studio is not proxied through the LMS, and shares
its database rather than asking the LMS for data.

Use the **internal** connection details. Sevalla also exposes an external endpoint
for each database, on a different hostname and a non-3306 port, meant for
connecting from a laptop. Pointing `MYSQL_HOST` at the external hostname while
leaving `MYSQL_PORT` at its `3306` default gets you:

```
MySQLdb.OperationalError: (2013, "Lost connection to MySQL server at
'reading initial communication packet', system error: 0")
```

That error occurs *before* authentication, so it never means a bad username,
password or database name — it means nothing that speaks MySQL is listening where
you pointed it. The database and both apps also have to be in the same region;
internal networking does not cross regions.

Whatever Sevalla injects when you connect a resource, `render_config.py` reads
only `MYSQL_HOST`, `MYSQL_DATABASE`, `MYSQL_USERNAME`, `MYSQL_PASSWORD`,
`REDIS_HOST` and the `S3_*` names, so those are the ones that must end up set.

### Domains

Each app gets a free `*.sevalla.app` domain on first deploy. Read the two
hostnames off the dashboard and set them as `LMS_HOST` and `CMS_HOST` — no
scheme, no trailing slash. Sevalla does not inject them for you.

```
LMS_HOST=openedx-lms-a1b2c.sevalla.app
CMS_HOST=openedx-cms-d4e5f.sevalla.app
```

TLS is covered by the wildcard certificate for `*.sevalla.app`, so keep
`ENABLE_HTTPS=true`. Leave `SESSION_COOKIE_DOMAIN` unset: each app then gets a
host-only cookie, which is correct when the two hostnames are unrelated. LMS and
Studio do not need to share cookies — Studio authenticates through the LMS over
OAuth2 and keeps its own `studio_session_id`.

**The MFEs are the catch.** `frontend-platform` reads the `edx-jwt-cookie-*`
cookies from `document.cookie`, and the LMS issues them scoped to
`SHARED_COOKIE_DOMAIN`, which derives from `SESSION_COOKIE_DOMAIN`. For an MFE to
authenticate, its origin has to sit under that domain. On free Sevalla domains
the only common parent is `sevalla.app` itself — and `sevalla.app` is **not** on
the Public Suffix List, so a browser *will* accept a cookie scoped to it and then
send your session and JWT cookies to every other tenant on the platform.
`render_config.py` refuses to do this and exits.

So: default `*.sevalla.app` domains are fine for bringing LMS and Studio up, and
fine for a single-app deployment. **Once you want MFEs, you need a custom domain**
with a shared parent, and then:

```
LMS_HOST=lms.yourdomain.com
CMS_HOST=studio.yourdomain.com
SESSION_COOKIE_DOMAIN=.yourdomain.com
MFE_ORIGINS=https://apps.yourdomain.com
```

## Environment variables

Required — the container exits at boot with a named error if any is missing:

| Variable | Notes |
| --- | --- |
| `OPENEDX_SECRET_KEY` | Django secret key. |
| `JWT_RSA_PRIVATE_KEY` | RSA private key, PEM. Signing JWKs are derived from it. |
| `LMS_HOST`, `CMS_HOST` | Hostnames, no scheme. e.g. `lms.example.com`. |
| `MYSQL_HOST`, `MYSQL_DATABASE`, `MYSQL_USERNAME`, `MYSQL_PASSWORD` | |
| `MONGODB_HOST` | Course content. Not optional — see Limitations. |
| `REDIS_HOST` | Cache and Celery broker. |
| `S3_BUCKET`, `S3_ENDPOINT`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | From the Sevalla Object Storage dashboard. |

Required on the **Studio app only**:

| Variable | Notes |
| --- | --- |
| `CMS_OAUTH2_KEY`, `CMS_OAUTH2_SECRET` | Studio authenticates against the LMS. Choose any values, then create a matching application in the LMS admin (below). |

Optional, with defaults:

| Variable | Default |
| --- | --- |
| `ENABLE_HTTPS` | `true` |
| `PLATFORM_NAME` | `Open edX` |
| `CONTACT_EMAIL` | `contact@example.com` |
| `LANGUAGE_CODE` | `en` |
| `MFE_ORIGINS` | *(empty)* — comma-separated origins **with scheme**, e.g. `https://apps.example.com`. Added to CORS and CSRF. |
| `SESSION_COOKIE_DOMAIN` | the app's own host — a host-only cookie. Set to a shared parent (`.yourdomain.com`) only on a domain you own, and only when you need MFE auth. Rejected for `sevalla.app`. |
| `EXTRA_ALLOWED_HOSTS` | *(empty)* — comma-separated extra `ALLOWED_HOSTS` entries, e.g. an internal health-check hostname. |
| `MYSQL_PORT` | `3306` |
| `MONGODB_PORT` / `MONGODB_DATABASE` | `27017` / `openedx` |
| `MONGODB_AUTH_SOURCE` / `MONGODB_USE_SSL` | `admin` / `true` |
| `REDIS_PORT` | `6379` |
| `REDIS_CACHE_DB` / `REDIS_CELERY_DB` | `1` / `0` |
| `S3_REGION` | `auto` |
| `S3_PREFIX` | `openedx` — key prefix, so environments can share a bucket. |
| `S3_URL_EXPIRY_SECONDS` | `3600` — presigned URL lifetime. |
| `JWT_AUDIENCE` | `openedx` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USE_TLS` | `localhost` / `587` / `true` |
| `MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB` | `100` |
| `PORT` / `UWSGI_WORKERS` | `8000` / `2` — Sevalla overrides `PORT`. |

May be left unset: `MONGODB_USERNAME`, `MONGODB_PASSWORD`, `MONGODB_REPLICA_SET`,
`REDIS_USERNAME`, `REDIS_PASSWORD`, `SMTP_USERNAME`, `SMTP_PASSWORD`.

`STATIC_ROOT_LMS` and `STATIC_ROOT_CMS` come from the base image. Do not override them.

### Copy-paste template

Every variable, with its default where one exists. Fill in the blanks; delete the
lines you are happy to leave at their default.

```sh
# ─── Required ────────────────────────────────────────────────────────────────
OPENEDX_SECRET_KEY=                      # openssl rand -hex 32
JWT_RSA_PRIVATE_KEY=                     # openssl genrsa 2048 -- see note below
LMS_HOST=                                # hostname only, no scheme
CMS_HOST=

MYSQL_HOST=
MYSQL_DATABASE=
MYSQL_USERNAME=
MYSQL_PASSWORD=

MONGODB_HOST=
REDIS_HOST=

S3_BUCKET=
S3_ENDPOINT=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=

# ─── Required on the Studio (CMS) app only ───────────────────────────────────
CMS_OAUTH2_KEY=
CMS_OAUTH2_SECRET=

# ─── Set on the Studio (CMS) app only ────────────────────────────────────────
SERVICE_VARIANT=cms
DJANGO_SETTINGS_MODULE=cms.envs.sevalla.production

# ─── Optional, defaults shown ────────────────────────────────────────────────
ENABLE_HTTPS=true
PLATFORM_NAME=Open edX
CONTACT_EMAIL=contact@example.com
LANGUAGE_CODE=en

MYSQL_PORT=3306

MONGODB_PORT=27017
MONGODB_DATABASE=openedx
MONGODB_AUTH_SOURCE=admin
MONGODB_USE_SSL=true

REDIS_PORT=6379
REDIS_CACHE_DB=1
REDIS_CELERY_DB=0

S3_REGION=auto
S3_PREFIX=openedx
S3_URL_EXPIRY_SECONDS=3600

JWT_AUDIENCE=openedx

SMTP_HOST=localhost
SMTP_PORT=587
SMTP_USE_TLS=true

MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB=100
UWSGI_WORKERS=2

# ─── Optional, empty by default ──────────────────────────────────────────────
MFE_ORIGINS=                             # https://apps.example.com,https://learn.example.com
SESSION_COOKIE_DOMAIN=                   # leave unset on *.sevalla.app domains
EXTRA_ALLOWED_HOSTS=

MONGODB_USERNAME=
MONGODB_PASSWORD=
MONGODB_REPLICA_SET=
REDIS_USERNAME=
REDIS_PASSWORD=
SMTP_USERNAME=
SMTP_PASSWORD=

# ─── Do not set ──────────────────────────────────────────────────────────────
# PORT              -- injected by Sevalla
# STATIC_ROOT_LMS   -- baked into the base image
# STATIC_ROOT_CMS   -- baked into the base image
```

`JWT_RSA_PRIVATE_KEY` is a multi-line PEM. Paste it into Sevalla's env var field
verbatim, newlines and all — it is read straight from the environment, not parsed
as shell. `OPENEDX_SECRET_KEY` and `JWT_RSA_PRIVATE_KEY` must be **identical on
both apps**, or Studio's tokens will be rejected by the LMS.

## First run

Migrations are run by hand, once, against the database. They are deliberately not
in the entrypoint: that script runs once per *container*, and a deployment has at
least two of them plus any replicas. Django takes no cross-process lock, so
concurrent `migrate` runs race, and the loser can leave a table created but not
recorded as applied.

### Getting a shell on an unmigrated deployment

An unmigrated deployment crash-loops. Django apps evaluate waffle switches while
loading, which queries MySQL; the table does not exist yet; and `need-app = true`
in `uwsgi.ini` makes a failed app import fatal rather than letting uwsgi serve
500s from a container that looks healthy. Good behaviour, but it means there is
no live pod to attach a web terminal to:

```
Error: Connection closed: There is no alive pod to connect to (code 4001)
```

Break the loop by overriding the app's start command with something that never
imports Django. The `ENTRYPOINT` is fixed in the image, so the config is still
rendered and a missing variable still fails loudly:

```sh
sh -c 'python -m http.server "$PORT"'
```

This binds `$PORT`, so the health check passes and the pod stays up. Redeploy,
open the terminal, migrate, then restore the start command to
`uwsgi /openedx/uwsgi.ini`.

### Migrating

The web terminal opens a process that never passed through the entrypoint, so use
the wrapper — it renders the config first:

```sh
openedx-manage lms migrate
openedx-manage cms migrate
openedx-manage lms createsuperuser
```

Run `cms migrate` from the **Studio app's** terminal. On the LMS app it fails with
`required environment variable CMS_OAUTH2_KEY is not set`, because
`cms/envs/sevalla/production.py` reads that variable at import time even though no
migration touches OAuth. Setting `CMS_OAUTH2_KEY` and `CMS_OAUTH2_SECRET` on the
LMS app too is a harmless workaround — nothing there reads them.

Then, so Studio can log in through the LMS, create a Django OAuth Toolkit
application at `https://<LMS_HOST>/admin/oauth2_provider/application/`:

- Client id: the value you set for `CMS_OAUTH2_KEY`
- Client secret: the value you set for `CMS_OAUTH2_SECRET`
- Client type: **Confidential**
- Grant type: **Authorization code**
- Redirect uri: `https://<CMS_HOST>/complete/edx-oauth2/`
- Skip authorization: checked

Studio will not authenticate until this exists.

## Limitations

Verified against this release, and none of them are configuration mistakes:

- **MongoDB is required.** Course content and course "Files & Uploads" live in
  the modulestore and `MongoContentStore` (GridFS). No object store replaces it.
- **Studio's video upload pipeline does not work.** It builds a boto v2
  `S3Connection` with no host override, so it always reaches `s3.amazonaws.com`.
  Either give it a real AWS bucket, or set `ENABLE_VIDEO_UPLOAD_PIPELINE` to
  false and have authors embed video by URL.
- **Uploaded files are served by presigned URLs** that expire after
  `S3_URL_EXPIRY_SECONDS`. Sevalla Object Storage buckets are private. Profile
  images work but do not cache well; a public bucket domain would fix that.
- **Course search is off.** It needs a Meilisearch instance, which is not
  provisioned. The dashboard and catalog work without it.
- **No Celery worker is deployed yet.** Grade report downloads, bulk email and
  course import/export are queued and will never run until one exists. It needs
  a third Sevalla app on the same image, same environment, overriding the
  command to run `celery --app=lms.celery worker`.
