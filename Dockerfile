# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# 21.0.8 is Tutor v21 == Open edX Ulmo (its translations are pulled from
# openedx-translations @ release/ulmo.3). Tutor v20 is Teak, v19 is Sumac.
#
# There is no `latest` tag in this repository -- the registry returns 404 for
# it -- so the tag has to be pinned to a release regardless.
#
# The image publishes linux/amd64 and linux/arm64. Sevalla runs amd64, so
# build with `--platform linux/amd64` on Apple Silicon.
# ---------------------------------------------------------------------------
FROM overhangio/openedx:21.0.8

# The base image carries edx-platform, its venv and all Python/Node deps, plus
# static assets already collected into /openedx/staticfiles. What it does NOT
# carry is a runtime settings module or config: it ships lms/envs/tutor/ with
# only __init__.py, assets.py and i18n.py, and /openedx/config/ with only
# revisions.yml. Tutor normally bind-mounts the rest in. We supply our own
# settings modules and generate their YAML at container start.

USER root

# Two settings modules, one per variant, each importing * from edx-platform's
# own production.py and then overriding it. common_all.py is shared, so it is
# copied into both packages.
RUN mkdir -p lms/envs/sevalla cms/envs/sevalla \
    && touch lms/envs/sevalla/__init__.py cms/envs/sevalla/__init__.py
COPY settings/common_all.py     lms/envs/sevalla/common_all.py
COPY settings/common_all.py     cms/envs/sevalla/common_all.py
COPY settings/lms_production.py lms/envs/sevalla/production.py
COPY settings/cms_production.py cms/envs/sevalla/production.py

# Overrides the base image's uwsgi.ini, which hardcodes port 8000.
COPY uwsgi.ini /openedx/uwsgi.ini
COPY bin/render_config.py bin/migrate_once.py \
     bin/sevalla-entrypoint.sh bin/openedx-manage /openedx/bin/

RUN chmod +x /openedx/bin/sevalla-entrypoint.sh /openedx/bin/openedx-manage \
    && mkdir -p /openedx/config /openedx/data/logs /openedx/media \
    && chown -R app:app /openedx/config /openedx/data /openedx/media \
                        /openedx/uwsgi.ini lms/envs/sevalla cms/envs/sevalla

USER app

# Our settings modules, not Tutor's. They read LMS_CFG / CMS_CFG, which the
# entrypoint renders from the environment on every boot.
ENV DJANGO_SETTINGS_MODULE=lms.envs.sevalla.production \
    SERVICE_VARIANT=lms \
    LMS_CFG=/openedx/config/lms.env.yml \
    CMS_CFG=/openedx/config/cms.env.yml

# uwsgi interpolates $(PORT) and $(UWSGI_WORKERS) from the environment and
# aborts if they are unset. Sevalla overrides PORT.
ENV PORT=8000 \
    UWSGI_WORKERS=2

# Run Studio from this same image by overriding, on the CMS app only:
#   SERVICE_VARIANT=cms
#   DJANGO_SETTINGS_MODULE=cms.envs.sevalla.production
# uwsgi then loads cms/wsgi.py instead of lms/wsgi.py.

EXPOSE 8000
ENTRYPOINT ["/openedx/bin/sevalla-entrypoint.sh"]
CMD ["uwsgi", "/openedx/uwsgi.ini"]
