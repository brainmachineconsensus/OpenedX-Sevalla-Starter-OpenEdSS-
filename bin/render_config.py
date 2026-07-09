#!/usr/bin/env python
"""
Render /openedx/config/{lms,cms}.env.yml from the environment.

edx-platform's lms/envs/production.py requires the LMS_CFG env var to point at
a YAML file and merges its top-level keys into the Django settings namespace.
The YAML itself has no interpolation, so on a platform where configuration
arrives as environment variables we have to generate it at container start.

Anything that cannot be expressed as a plain YAML value -- function calls, list
mutations, key derivation -- lives in settings/common_all.py instead.
"""
import os
import sys

import yaml

CONFIG_DIR = "/openedx/config"

# Shared PaaS domains that every tenant's app sits under. None of these are on
# the Public Suffix List, so a browser will happily accept a cookie scoped to
# them -- and then send it to every other tenant on the platform.
SHARED_PAAS_DOMAINS = {"sevalla.app"}


class MissingSetting(Exception):
    pass


class UnsafeCookieDomain(Exception):
    pass


def req(name):
    value = os.environ.get(name)
    if not value:
        raise MissingSetting(name)
    return value


def opt(name, default=""):
    return os.environ.get(name) or default


def redis_url(db):
    user = opt("REDIS_USERNAME")
    password = opt("REDIS_PASSWORD")
    auth = f"{user}:{password}@" if password else ""
    return f"redis://{auth}{req('REDIS_HOST')}:{opt('REDIS_PORT', '6379')}/{db}"


def cache(prefix, timeout=None):
    entry = {
        "KEY_PREFIX": prefix,
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": redis_url(opt("REDIS_CACHE_DB", "1")),
    }
    if timeout is not None:
        entry["TIMEOUT"] = timeout
    return entry


def cookie_domain(host):
    """
    Scopes the session and JWT cookies. SHARED_COOKIE_DOMAIN derives from this,
    and the MFE auth cookies are issued against it, so MFEs only authenticate
    when their origin sits under this domain.

    Defaulting to the exact host keeps the cookie host-only, which is what you
    want on Sevalla's free *.sevalla.app domains. Widening it to a shared parent
    is only safe on a domain you own -- see the guard below.
    """
    domain = opt("SESSION_COOKIE_DOMAIN", host)
    bare = domain.lstrip(".")
    if bare in SHARED_PAAS_DOMAINS:
        raise UnsafeCookieDomain(bare)
    return domain


def base_config(variant):
    https = opt("ENABLE_HTTPS", "true").lower() == "true"
    scheme = "https" if https else "http"
    lms_host, cms_host = req("LMS_HOST"), req("CMS_HOST")
    host = lms_host if variant == "lms" else cms_host

    extra_hosts = [h.strip() for h in opt("EXTRA_ALLOWED_HOSTS").split(",") if h.strip()]

    return {
        "SECRET_KEY": req("OPENEDX_SECRET_KEY"),
        "SITE_NAME": host,
        "LMS_BASE": lms_host,
        "CMS_BASE": cms_host,
        "LMS_ROOT_URL": f"{scheme}://{lms_host}",
        "CMS_ROOT_URL": f"{scheme}://{cms_host}",
        # Sevalla terminates TLS and proxies to the container, so the app also
        # has to trust its own external hostname.
        "ALLOWED_HOSTS": [host, "localhost", "127.0.0.1"] + extra_hosts,
        "SESSION_COOKIE_DOMAIN": cookie_domain(host),
        "HTTPS": "on" if https else "off",
        "PLATFORM_NAME": opt("PLATFORM_NAME", "Open edX"),
        "CONTACT_EMAIL": opt("CONTACT_EMAIL", "contact@example.com"),
        "LANGUAGE_CODE": opt("LANGUAGE_CODE", "en"),
        "OAUTH_OIDC_ISSUER": f"{scheme}://{lms_host}/oauth2",
        "BOOK_URL": "",
        "LOG_DIR": "/openedx/data/logs",
        "LOGGING_ENV": "sandbox",
        # Collected into the image at build time; served by uwsgi's static-map.
        "STATIC_ROOT_BASE": os.environ[f"STATIC_ROOT_{variant.upper()}"],
        "STATIC_URL_BASE": "/static/" if variant == "lms" else "/static/studio/",
        "ENABLE_COMPREHENSIVE_THEMING": True,
        "XQUEUE_INTERFACE": {"django_auth": None, "url": None},
        # Replaced with a dict by settings/common_all.py:configure_mongodb.
        "DOC_STORE_CONFIG": None,
        "DATABASES": {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "HOST": req("MYSQL_HOST"),
                "PORT": int(opt("MYSQL_PORT", "3306")),
                "NAME": req("MYSQL_DATABASE"),
                "USER": req("MYSQL_USERNAME"),
                "PASSWORD": req("MYSQL_PASSWORD"),
                "ATOMIC_REQUESTS": True,
                "OPTIONS": {
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                    "charset": "utf8mb4",
                },
            }
        },
        "CACHES": {
            "default": dict(cache("default"), VERSION="1"),
            "general": cache("general"),
            "configuration": cache("configuration"),
            "celery": cache("celery", timeout=7200),
            "mongo_metadata_inheritance": cache("mongo_metadata_inheritance", timeout=300),
            "course_structure_cache": cache("course_structure", timeout=604800),
            "ora2-storage": cache("ora2-storage"),
        },
        "CELERY_BROKER_TRANSPORT": "redis",
        "CELERY_BROKER_HOSTNAME": f"{req('REDIS_HOST')}:{opt('REDIS_PORT', '6379')}",
        "CELERY_BROKER_VHOST": opt("REDIS_CELERY_DB", "0"),
        "CELERY_BROKER_USER": opt("REDIS_USERNAME"),
        "CELERY_BROKER_PASSWORD": opt("REDIS_PASSWORD"),
        # Each variant consumes the other's queue.
        "ALTERNATE_WORKER_QUEUES": "cms" if variant == "lms" else "lms",
        "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_HOST": opt("SMTP_HOST", "localhost"),
        "EMAIL_PORT": int(opt("SMTP_PORT", "587")),
        "EMAIL_USE_TLS": opt("SMTP_USE_TLS", "true").lower() == "true",
        "EMAIL_HOST_USER": opt("SMTP_USERNAME"),
        "EMAIL_HOST_PASSWORD": opt("SMTP_PASSWORD"),
        "FEATURES": features(variant),
    }


def features(variant):
    shared = {
        "CERTIFICATES_HTML_VIEW": True,
        "ENABLE_CSMH_EXTENDED": False,
        "ENABLE_LEARNER_RECORDS": False,
        "ENABLE_PREREQUISITE_COURSES": True,
        "MILESTONES_APP": True,
    }
    if variant == "lms":
        shared.update(
            {
                "ENABLE_COMBINED_LOGIN_REGISTRATION": True,
                "ENABLE_GRADE_DOWNLOADS": True,
                "ENABLE_MOBILE_REST_API": True,
                "ENABLE_OAUTH2_PROVIDER": True,
                "ENABLE_THIRD_PARTY_AUTH": True,
            }
        )
    else:
        shared["ENABLE_LIBRARY_INDEX"] = True
    return shared


def main():
    try:
        # Render both variants up front. Opening the target first would
        # truncate a previously good config before we know this one is valid.
        rendered = {
            variant: yaml.safe_dump(base_config(variant), default_flow_style=False)
            for variant in ("lms", "cms")
        }
    except MissingSetting as exc:
        sys.exit(f"error: required environment variable {exc} is not set")
    except KeyError as exc:
        sys.exit(f"error: required environment variable {exc} is not set")
    except UnsafeCookieDomain as exc:
        sys.exit(
            f"error: refusing to scope session cookies to '{exc}'.\n"
            f"       '{exc}' is shared by every app on the platform and is not a\n"
            "       public suffix, so the browser would send your session and JWT\n"
            "       cookies to other tenants. Leave SESSION_COOKIE_DOMAIN unset to\n"
            "       get a host-only cookie, or set it to a domain you own."
        )

    os.makedirs(CONFIG_DIR, exist_ok=True)
    for variant, text in rendered.items():
        target = os.path.join(CONFIG_DIR, f"{variant}.env.yml")
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, target)  # atomic; readers never see a partial file
        print(f"rendered {target}", file=sys.stderr)


if __name__ == "__main__":
    main()
