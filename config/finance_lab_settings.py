"""Only the disposable runner uses this URLConf; never imported by normal startup."""
import os
from pathlib import Path
from .settings import *  # noqa: F403

_expected = os.environ.get("EXPECTED_DISPOSABLE_DATA", "")
if not _expected.startswith("/tmp/tivoli-finance-lab.") or Path(_expected).name != "data":
    raise RuntimeError("Finance lab requires the dedicated disposable runner.")
if not str(DATABASES["default"]["NAME"]).startswith("test_"):  # noqa: F405
    raise RuntimeError("Finance lab requires an isolated test database.")
ROOT_URLCONF = "foundation.finance_lab_urls"
FINANCE_DRAFT_TEST_MODE = True
FINANCE_LAB_ENABLED = True
INSTALLED_APPS = [*INSTALLED_APPS, "finance_allocation_lab"]  # noqa: F405
FINANCE_LAB_DATA_DIRECTORY = _expected
FINANCE_LAB_ACTOR = "synthetic_finance_lab"
FINANCE_LAB_ENTITY_CODE = "test-finance-lab"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
DEBUG = False
