# UCSB Scheduler Optimizer

A Winter 2027 course scheduler that imports the UCSB Academic Curriculums
catalog, builds valid lecture and secondary-section combinations, removes time
conflicts, and ranks schedules against student preferences.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Create `backend/.env` with your UCSB API key:

```text
UCSB_API_KEY=your_key_here
```

Build or validate the Winter 2027 catalog:

```bash
PYTHONPATH=backend python backend/bootstrap_catalog.py
```

## Local Development

Start the API:

```bash
PYTHONPATH=backend python backend/api.py
```

Start Vite in a second terminal:

```bash
cd frontend
npm ci
npm run dev -- --port 5180
```

Open `http://127.0.0.1:5180`. Vite proxies `/api` requests to the Flask server
at `http://127.0.0.1:5000`.

## Production Website

The production build runs as one website. Flask serves both the compiled React
interface and `/api/v1`, while Gunicorn runs the web process.

```bash
docker build -t ucsb-scheduler .
docker run --rm \
  --env-file backend/.env \
  -p 8000:8000 \
  -v "$(pwd)/instance:/app/instance" \
  ucsb-scheduler
```

Open `http://127.0.0.1:8000`. In hosted environments, set
`SCHEDULER_DATABASE_PATH` to a writable persistent volume. Keep
`UCSB_API_KEY` in the host's secret manager and never expose it through a
`VITE_*` variable.

See [`docs/deployment.md`](docs/deployment.md) for the production environment,
startup behavior, and deployment checklist.

## Tests

```bash
PYTHONPATH=backend python -m unittest discover -s tests
cd frontend
npm test
npm run lint
npm run build
```

The API contract is documented in
[`docs/frontend-api-contract.md`](docs/frontend-api-contract.md).
