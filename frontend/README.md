# Gaucho Route Builder Frontend

The React interface searches the Winter 2027 UCSB catalog and sends schedule
requests to the Python API. Conflict detection, scoring, and ranking remain in
the backend.

## Prerequisites

Start the API from the repository root:

```bash
source .venv/bin/activate
PYTHONPATH=backend python backend/api.py
```

## Run locally

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:5000` during local development. To
use another API, create `frontend/.env`:

```text
VITE_API_BASE_URL=https://example.com/api/v1
```

Build and lint with:

```bash
npm test
npm run build
npm run lint
```

The production build is served by Flask from `frontend/dist`, so the website
and API share one origin.
