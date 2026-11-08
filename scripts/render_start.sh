#!/usr/bin/env bash
set -euo pipefail

# Build the bundle at startup if a Dashboard override skipped build.sh.
if [[ ! -s dist/index.html ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "Frontend bundle is missing and npm is unavailable. Set Render Build Command to: bash build.sh" >&2
    exit 1
  fi
  echo "Frontend bundle missing; building it before starting Django." >&2
  npm ci --include=dev
  npm run build
fi
if [[ ! -s dist/index.html ]]; then
  echo "Frontend build failed: dist/index.html is missing. Set Render Build Command to: bash build.sh" >&2
  exit 1
fi
python manage.py collectstatic --noinput
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
