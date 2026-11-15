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
- `POST /api/v1/sessions/{session_id}/advance` — calculate and persist the next period.

The backend is the authoritative state owner. Clients submit domain commands; they do not send replacement game-state snapshots. Period advancement is performed in a database transaction and locks the session row while calculating the next state.

## Current development boundary

The first vertical slice implements the Ares-1 oxygen, food, water, raw-ice, energy-summary and operating-budget balances. Role permissions, player authentication, project execution, detailed energy dispatch, and WebSocket synchronization are subsequent work packages. The initial API is intended for a trusted development LAN; authentication and authorization are required before exposing it beyond that environment.

The schema is versioned with Alembic. Scenario JSON remains the versioned source for initial conditions and scripted events.
