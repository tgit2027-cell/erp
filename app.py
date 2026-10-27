"""WSGI alias for Render services configured with the default ``app:app`` command."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if not (PROJECT_ROOT / "config" / "wsgi.py").is_file():
    raise RuntimeError(
        "The Django config/wsgi.py package is missing from the deployed repository root. "
        "Deploy the complete project, including the config/ directory."
    )

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.wsgi import application as app  # noqa: E402
