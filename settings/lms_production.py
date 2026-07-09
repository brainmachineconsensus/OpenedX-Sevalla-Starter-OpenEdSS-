"""
LMS production settings. Copied into the image as lms/envs/sevalla/production.py.

Importing * from lms.envs.production runs edx-platform's whole production
pipeline first -- YAML merge, derived settings, plugin settings -- so every
name below overrides a finished value rather than racing it.
"""
# pylint: disable=wildcard-import, unused-wildcard-import
from lms.envs.production import *

import sys

from .common_all import apply_common

apply_common(sys.modules[__name__])

# LocMem rather than Redis: this cache holds the staticfiles manifest, which is
# baked into the image and identical for the life of the container.
CACHES["staticfiles"] = {
    "KEY_PREFIX": "staticfiles_lms",
    "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    "LOCATION": "staticfiles_lms",
}

REGISTRATION_EXTRA_FIELDS["terms_of_service"] = "hidden"
REGISTRATION_EXTRA_FIELDS["honor_code"] = "hidden"

COURSE_CATALOG_VISIBILITY_PERMISSION = "see_in_catalog"
COURSE_ABOUT_VISIBILITY_PERMISSION = "see_about_page"

DEFAULT_EMAIL_LOGO_URL = LMS_ROOT_URL + "/theming/asset/images/logo.png"
BULK_EMAIL_SEND_USING_EDX_ACE = True

# Studio's logout has to tear down the LMS session it authenticated against.
IDA_LOGOUT_URI_LIST.append(CMS_ROOT_URL + "/logout/")

# Show courses on the landing page whose start date has not yet passed.
SEARCH_SKIP_ENROLLMENT_START_DATE_FILTERING = True

# Course search is left off: it requires a Meilisearch instance, which is not
# provisioned. The dashboard and course catalog work without it.
