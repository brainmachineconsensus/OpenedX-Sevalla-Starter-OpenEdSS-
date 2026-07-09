"""
Settings shared by LMS and CMS that cannot be expressed as YAML.

Everything that is a plain value lives in the YAML that bin/render_config.py
generates. What is left here needs Python: function calls, list mutations, key
derivation, and settings that edx-platform's own production.py assigns *after*
it merges the YAML, where a YAML key would simply be overwritten.

Imported by lms/envs/sevalla/production.py and cms/envs/sevalla/production.py,
each of which has already run `from <variant>.envs.production import *`, so
`settings` here is a fully-populated settings module.
"""
import base64
import json
import os

S3_BACKEND = "storages.backends.s3boto3.S3Boto3Storage"


def env(name, default=None):
    value = os.environ.get(name)
    if value:
        return value
    if default is None:
        raise RuntimeError(f"required environment variable {name} is not set")
    return default


def s3_options(location=""):
    """
    Connection kwargs for Sevalla Object Storage, which is Cloudflare R2
    behind an S3 API.

    default_acl must be None: edx-platform's production.py sets
    AWS_DEFAULT_ACL = 'public-read', django-storages would turn that into an
    ACL header on every PutObject, and R2 does not implement ACLs.

    addressing_style must be 'path': R2 does not resolve bucket.endpoint.
    """
    return {
        "bucket_name": env("S3_BUCKET"),
        "endpoint_url": env("S3_ENDPOINT"),
        "access_key": env("S3_ACCESS_KEY_ID"),
        "secret_key": env("S3_SECRET_ACCESS_KEY"),
        "region_name": env("S3_REGION", "auto"),
        "addressing_style": "path",
        "default_acl": None,
        # Objects are private, so url() hands out expiring presigned links.
        "querystring_auth": True,
        "querystring_expire": int(env("S3_URL_EXPIRY_SECONDS", "3600")),
        "location": location,
    }


def configure_storage(settings):
    prefix = env("S3_PREFIX", "openedx")

    # Course exports, grade CSVs, ORA attachments -- arbitrary file types.
    settings.STORAGES["default"] = {
        "BACKEND": S3_BACKEND,
        "OPTIONS": s3_options(f"{prefix}/uploads"),
    }
    # Read in preference to PROFILE_IMAGE_BACKEND; overwrite in place, since
    # a user's image name is derived from their id and must be replaceable.
    settings.STORAGES["profile_image"] = {
        "BACKEND": S3_BACKEND,
        "OPTIONS": dict(s3_options(f"{prefix}/profile-images"), file_overwrite=True),
    }
    # Kept for releases that predate the STORAGES['profile_image'] key.
    settings.PROFILE_IMAGE_BACKEND = {
        "class": S3_BACKEND,
        "options": dict(s3_options(f"{prefix}/profile-images"), file_overwrite=True),
    }

    # STORAGES['staticfiles'] is deliberately untouched: assets are collected
    # into the image at build time and served by uwsgi's static-map.

    # Report stores build their own storage rather than using the default one.
    settings.GRADES_DOWNLOAD = {
        "STORAGE_CLASS": S3_BACKEND,
        "STORAGE_KWARGS": s3_options(f"{prefix}/grades"),
        "STORAGE_TYPE": "",
        "BUCKET": None,
        "ROOT_PATH": None,
    }

    # Default STORAGE_KWARGS point 'location' at a filesystem path, which
    # would land these on the container's ephemeral disk.
    settings.VIDEO_IMAGE_SETTINGS["STORAGE_CLASS"] = S3_BACKEND
    settings.VIDEO_IMAGE_SETTINGS["STORAGE_KWARGS"] = s3_options(f"{prefix}/video-images")
    settings.VIDEO_TRANSCRIPTS_SETTINGS["STORAGE_CLASS"] = S3_BACKEND
    settings.VIDEO_TRANSCRIPTS_SETTINGS["STORAGE_KWARGS"] = s3_options(
        f"{prefix}/video-transcripts"
    )

    # 'django' routes ORA uploads through STORAGES['default'] above; the
    # 'filesystem' default would lose them on restart.
    settings.ORA2_FILEUPLOAD_BACKEND = "django"
    settings.ORA2_FILEUPLOAD_CACHE_NAME = "ora2-storage"

    # Studio's video upload pipeline is NOT configured here. It talks to S3
    # through boto's S3Connection with no host override, so it always reaches
    # s3.amazonaws.com and cannot be pointed at R2. Either give it a real AWS
    # bucket or set FEATURES['ENABLE_VIDEO_UPLOAD_PIPELINE'] = False.


def configure_mongodb(settings):
    """
    Course content lives in MongoDB regardless of the object store: the
    modulestore and the contentstore ("Files & Uploads") both target it.
    """
    doc_store = {
        "db": env("MONGODB_DATABASE", "openedx"),
        "host": env("MONGODB_HOST"),
        "port": int(env("MONGODB_PORT", "27017")),
        "user": os.environ.get("MONGODB_USERNAME") or None,
        "password": os.environ.get("MONGODB_PASSWORD") or None,
        "connect": False,
        "ssl": env("MONGODB_USE_SSL", "true").lower() == "true",
        "authsource": env("MONGODB_AUTH_SOURCE", "admin"),
        "replicaSet": os.environ.get("MONGODB_REPLICA_SET") or None,
    }

    settings.DOC_STORE_CONFIG = doc_store
    settings.CONTENTSTORE = {
        "ENGINE": "xmodule.contentstore.mongo.MongoContentStore",
        "ADDITIONAL_OPTIONS": {},
        "DOC_STORE_CONFIG": doc_store,
    }

    # Imported lazily: xmodule is only importable from inside edx-platform.
    from xmodule.modulestore.modulestore_settings import update_module_store_settings

    update_module_store_settings(settings.MODULESTORE, doc_store_settings=doc_store)

    settings.DATA_DIR = "/openedx/data/modulestore"
    for store in settings.MODULESTORE["default"]["OPTIONS"]["stores"]:
        store["OPTIONS"]["fs_root"] = settings.DATA_DIR


def _b64(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def configure_jwt(settings):
    """
    Derive the signing JWKs from a PEM private key so the deployment only has
    to carry one secret. The key must be stable across restarts and identical
    in LMS and CMS, otherwise tokens minted by one are rejected by the other.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private = load_pem_private_key(env("JWT_RSA_PRIVATE_KEY").encode(), password=None)
    numbers = private.private_numbers()
    public = numbers.public_numbers

    settings.JWT_AUTH["JWT_ISSUER"] = settings.OAUTH_OIDC_ISSUER
    settings.JWT_AUTH["JWT_AUDIENCE"] = env("JWT_AUDIENCE", "openedx")
    settings.JWT_AUTH["JWT_SECRET_KEY"] = settings.SECRET_KEY
    settings.JWT_AUTH["JWT_PRIVATE_SIGNING_JWK"] = json.dumps(
        {
            "kid": "openedx",
            "kty": "RSA",
            "e": _b64(public.e),
            "n": _b64(public.n),
            "d": _b64(numbers.d),
            "p": _b64(numbers.p),
            "q": _b64(numbers.q),
            "dp": _b64(numbers.dmp1),
            "dq": _b64(numbers.dmq1),
            "qi": _b64(numbers.iqmp),
        }
    )
    settings.JWT_AUTH["JWT_PUBLIC_SIGNING_JWK_SET"] = json.dumps(
        {"keys": [{"kid": "openedx", "kty": "RSA", "e": _b64(public.e), "n": _b64(public.n)}]}
    )
    settings.JWT_AUTH["JWT_ISSUERS"] = [
        {
            "ISSUER": settings.OAUTH_OIDC_ISSUER,
            "AUDIENCE": env("JWT_AUDIENCE", "openedx"),
            "SECRET_KEY": settings.SECRET_KEY,
        }
    ]


def configure_cors(settings):
    """
    The MFEs are separate Sevalla static sites on their own origins, so they
    are cross-origin to the LMS and need both CORS and CSRF clearance.
    """
    mfe_origins = [
        origin.strip()
        for origin in os.environ.get("MFE_ORIGINS", "").split(",")
        if origin.strip()
    ]
    origins = [settings.LMS_ROOT_URL, settings.CMS_ROOT_URL] + mfe_origins

    settings.FEATURES["ENABLE_CORS_HEADERS"] = True
    settings.CORS_ALLOW_CREDENTIALS = True
    settings.CORS_ORIGIN_ALLOW_ALL = False
    settings.CORS_ORIGIN_WHITELIST = origins
    settings.CORS_ALLOW_INSECURE = False
    settings.CSRF_TRUSTED_ORIGINS = origins
    settings.LOGIN_REDIRECT_WHITELIST = [settings.CMS_BASE] + mfe_origins

    # Sevalla terminates TLS upstream, so the app never sees an https request
    # line and would otherwise mark cookies insecure.
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.SESSION_COOKIE_SAMESITE = "None"
    settings.SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    settings.X_FRAME_OPTIONS = "SAMEORIGIN"


def configure_logging(settings):
    """Replace the syslog/file handlers with stdout, which is what Sevalla collects."""
    for handler in ("local", "tracking"):
        settings.LOGGING["handlers"][handler] = {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        }
    settings.LOGGING["loggers"]["tracking"]["handlers"] = ["console"]


def rebind_features(settings):
    """
    FEATURES is a FeaturesProxy that flattens each flag into the settings
    namespace it was constructed with. production.py binds it to *its own*
    globals, so after `from ... import *` the inherited proxy still writes
    there, and settings.FEATURES['X'] and settings.X drift apart. Rebinding it
    onto this module's namespace keeps them in sync, which is what upstream
    does. Outside edx-platform (i.e. in tests) FEATURES stays a plain dict.
    """
    try:
        from openedx.core.lib.features_setting_proxy import FeaturesProxy
    except ImportError:
        return
    settings.FEATURES = FeaturesProxy(vars(settings))


def apply_common(settings):
    rebind_features(settings)

    settings.SITE_ID = 2
    settings.MEDIA_ROOT = "/openedx/media/"
    settings.DJANGO_REDIS_IGNORE_EXCEPTIONS = True

    configure_storage(settings)
    configure_mongodb(settings)
    configure_jwt(settings)
    configure_cors(settings)
    configure_logging(settings)

    settings.FEATURES["ENABLE_DISCUSSION_SERVICE"] = False
    settings.FEATURES["PREVENT_CONCURRENT_LOGINS"] = False

    # Route ACE mail through Django's configured SMTP backend.
    settings.ACE_ENABLED_CHANNELS = ["django_email"]
    settings.ACE_CHANNEL_DEFAULT_EMAIL = "django_email"
    settings.ACE_CHANNEL_TRANSACTIONAL_EMAIL = "django_email"

    # codejail needs an explicit unusable interpreter, or the prod defaults
    # leave it configured to execute untrusted code unsandboxed.
    import codejail.jail_code

    codejail.jail_code.configure("python", "nonexistingpythonbinary", user=None)
    settings.CODE_JAIL = {"python_bin": "nonexistingpythonbinary", "user": None}

    # We do not run the CSMH-extended database.
    if "lms.djangoapps.coursewarehistoryextended" in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS.remove("lms.djangoapps.coursewarehistoryextended")
    router = "openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter"
    if router in settings.DATABASE_ROUTERS:
        settings.DATABASE_ROUTERS.remove(router)

    for folder in (settings.LOG_DIR, settings.MEDIA_ROOT, settings.DATA_DIR):
        os.makedirs(folder, exist_ok=True)
