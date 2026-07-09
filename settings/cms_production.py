"""
Studio production settings. Copied into the image as cms/envs/sevalla/production.py.

Importing * from cms.envs.production runs edx-platform's whole production
pipeline first -- YAML merge, derived settings, plugin settings -- so every
name below overrides a finished value rather than racing it.
"""
# pylint: disable=wildcard-import, unused-wildcard-import
from cms.envs.production import *

import os
import sys

from .common_all import apply_common, env

apply_common(sys.modules[__name__])

STUDIO_NAME = f"{PLATFORM_NAME} - Studio"

CACHES["staticfiles"] = {
    "KEY_PREFIX": "staticfiles_cms",
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "staticfiles_cms",
}

# Studio authenticates its users against the LMS over OAuth2. These must match
# a Django OAuth Toolkit application created in the LMS admin, which can only
# exist after the first migration has run.
SOCIAL_AUTH_EDX_OAUTH2_KEY = env("CMS_OAUTH2_KEY")
SOCIAL_AUTH_EDX_OAUTH2_SECRET = env("CMS_OAUTH2_SECRET")
SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT = LMS_ROOT_URL
SOCIAL_AUTH_EDX_OAUTH2_PUBLIC_URL_ROOT = LMS_ROOT_URL
SOCIAL_AUTH_REDIRECT_IS_HTTPS = True

# Distinct from the LMS cookie, which would otherwise clobber it whenever the
# two share a parent domain.
SESSION_COOKIE_NAME = "studio_session_id"

FRONTEND_LOGIN_URL = LMS_ROOT_URL + "/login"
FRONTEND_REGISTER_URL = LMS_ROOT_URL + "/register"

MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB = int(
    os.environ.get("MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB", "100")
)
