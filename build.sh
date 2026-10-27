#!/usr/bin/env bash
set -euo pipefail

python -m pip install --no-cache-dir -r requirements.txt
npm ci --include=dev
npm run build
python manage.py collectstatic --noinput
