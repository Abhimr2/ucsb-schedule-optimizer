FROM node:24-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist/
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN useradd --create-home scheduler \
    && mkdir -p /app/instance \
    && chown -R scheduler:scheduler /app \
    && chmod +x /app/docker-entrypoint.sh

USER scheduler
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/status' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
