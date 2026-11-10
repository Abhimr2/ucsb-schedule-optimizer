# Website Deployment

The production application is one web service:

```text
Browser -> Flask and Gunicorn -> SQLite catalog
             |-- /          React website
             |-- /assets/*  compiled assets
             |-- /api/v1/*  scheduler API
```

## Required Environment

| Variable | Purpose |
| --- | --- |
| `UCSB_API_KEY` | Server-side UCSB Academic Curriculums API credential |
| `SCHEDULER_DATABASE_PATH` | Writable SQLite path, preferably on persistent storage |
| `PORT` | Public web process port; defaults to `8000` |
| `SYNC_CATALOG_ON_START` | Set to `1` to attempt a fresh catalog before startup |
| `WEB_CONCURRENCY` | Gunicorn worker count; defaults to `2` |
| `WEB_THREADS` | Threads per worker; defaults to `4` |

Do not create a `VITE_UCSB_API_KEY`. Any `VITE_*` value is compiled into the
browser bundle and can be read by website visitors.

## Catalog Startup

`backend/bootstrap_catalog.py` validates SQLite integrity, checks that Winter
2027 courses and sections exist, and confirms a completed sync. When a refresh
is needed, it downloads into a temporary database and replaces the active file
only after validation succeeds. If a refresh fails and the previous catalog is
valid, the website starts with the previous snapshot.

For persistent storage, use a path such as:

```text
SCHEDULER_DATABASE_PATH=/data/catalog.db
```

SQLite also creates `-wal` and `-shm` files, so the containing directory must
be writable.

## Container Build

```bash
docker build -t ucsb-scheduler .
docker run --rm \
  -e UCSB_API_KEY="your-key" \
  -e SCHEDULER_DATABASE_PATH=/data/catalog.db \
  -e SYNC_CATALOG_ON_START=1 \
  -p 8000:8000 \
  -v scheduler-data:/data \
  ucsb-scheduler
```

Verify these routes after deployment:

```text
GET /
GET /api/v1/status
GET /api/v1/courses?q=CMPSC%2016
```

## Release Checklist

1. Commit the source, lockfile, Docker configuration, and tests.
2. Store `UCSB_API_KEY` as a secret, never in Git.
3. Attach writable persistent storage for the catalog.
4. Build the container and wait for the catalog bootstrap to complete.
5. Confirm `/api/v1/status` reports Winter 2027 and a successful snapshot.
6. Search for two courses and generate schedules in the deployed website.
7. Check the website at desktop and mobile widths.
