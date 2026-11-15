# MarsColony backend

## Local run

From the repository root:

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`; interactive OpenAPI documentation is at `http://localhost:8000/docs`.

For local development without Docker, install `backend/requirements.txt`, set `DATABASE_URL` to a reachable PostgreSQL database, run `alembic -c backend/alembic.ini upgrade head` from the repository root, then start Uvicorn with:

```bash
uvicorn backend.app.main:app --reload
```

## Initial API

- `GET /health` — process health.
- `POST /api/v1/sessions` — create a session from a versioned scenario. Body: `{"scenario_id":"ares-01"}`.
- `GET /api/v1/sessions/{session_id}` — read the current state and append-only event history.
- `POST /api/v1/sessions/{session_id}/advance` — calculate and persist the next period. Optional body: `{"expected_period":0}`. Supplying the period observed by the client prevents a stale or repeated command from advancing a newer state.

The backend is the authoritative state owner. Clients submit domain commands; they do not send replacement game-state snapshots. Period advancement is performed in a database transaction and locks the session row while calculating the next state.

Each session stores the exact scenario definition used at creation. Later edits to the scenario file therefore affect new sessions while existing sessions continue with their original parameters.

## Domain calculation modules

- `backend/app/domain/engine.py` — Ares-1 campaign calculation and compatibility layer.
- `backend/app/domain/resources.py` — deterministic material-process execution, input constraints, energy use and storage capacity.
- `backend/app/domain/energy.py` — priority-based electrical load dispatch with minimum stable loads.

The resource and energy modules are scenario-independent domain primitives. The Ares-1 engine already uses them for oxygen production, greenhouse output, ice-to-water processing and the reactor-service dispatch. Process energy demand is reported separately from the aggregate electrical balance.

## Persistence and validation

The schema is versioned with Alembic. Scenario snapshots and session state are stored in PostgreSQL; each completed period appends a report event in the same transaction. The CI workflow applies migrations to PostgreSQL 16 before running the integration suite against that database. It also builds and starts the complete Compose stack, creates and advances a session, restarts PostgreSQL and the API containers, and verifies that the persisted session state and event history are still available.

The Compose stack starts PostgreSQL and the API. Database credentials can be overridden with `POSTGRES_PASSWORD`; the default is intended for local development only.

## Current development boundary

The Ares-1 engine calculates the maintenance interval separately from the normal operating interval and reports generator output, served load, battery reserve requirement and curtailed flexible demand. Battery state-of-charge, charge/discharge power limits and efficiency still require implementation. Projects, player permissions, authentication, WebSocket synchronization and frontend belong to subsequent stages. The initial API is intended for a trusted development LAN; authentication and authorization are required before exposing it beyond that environment.
