#!/usr/bin/env bash
set -euo pipefail

python -m pip install --no-cache-dir -r requirements.txt
npm ci --include=dev
npm run build
test -s dist/index.html || { echo "Frontend build failed: dist/index.html is missing." >&2; exit 1; }
python manage.py collectstatic --noinput
