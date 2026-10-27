# syntax=docker/dockerfile:1
FROM node:24-alpine AS frontend
ARG VITE_BASE_PATH=/static/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
WORKDIR /frontend
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=demo \
    DJANGO_DEBUG=false \
    DJANGO_MEDIA_ROOT=/var/data/media
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . ./
COPY --from=frontend /frontend/dist ./dist
RUN python manage.py collectstatic --noinput
EXPOSE 10000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers ${WEB_CONCURRENCY:-2} --access-logfile - --error-logfile -"]
