#!/usr/bin/env bash
set -euo pipefail

python manage.py migrate --noinput
python manage.py seed_demo
if [[ -n "${RENDER_INITIAL_ADMIN_PASSWORD:-}" ]]; then
  python manage.py create_render_admin
fi
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:?Render PORT is required}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --access-logfile - \
  --error-logfile -
