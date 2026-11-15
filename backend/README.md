# MarsColony backend

## Local run

From the repository root:

```bash
docker compose up --build
```

The web client is available at http://localhost:8080. The API is available at http://localhost:8000. OpenAPI documentation is at http://localhost:8000/docs.

For local development without Docker, install `backend/requirements.txt`, set `DATABASE_URL` to a reachable PostgreSQL database, run Alembic migrations from the repository root, then start Uvicorn:

```bash
pip install -r backend/requirements.txt
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --reload
```

## HTTP API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /health | API process health |
| GET | /health/ready | API and PostgreSQL readiness |
| POST | /api/v1/sessions | Create a session and receive owner credentials |
| POST | /api/v1/sessions/{session_id}/players | Join using the session invitation code |
| GET | /api/v1/sessions/{session_id}/players | List session participants |
| PUT | /api/v1/sessions/{session_id}/players/{player_id}/roles | Assign roles and responsibility domains |
| GET | /api/v1/sessions/{session_id} | Read session state and event history |
| POST | /api/v1/sessions/{session_id}/advance | Atomically calculate and persist the next period |
| POST | /api/v1/sessions/{session_id}/projects | Create a project proposal |
| GET | /api/v1/sessions/{session_id}/projects | List project proposals |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/submit | Submit a draft and reserve budget |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/approvals | Approve or reject a proposal |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/execute | Start a project |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/complete | Complete a project |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/cancel | Cancel a project before execution |

All participant endpoints use the `X-Player-Token` header. Tokens are scoped to one session. The server checks authorization for every command. Clients never submit replacement game-state snapshots.

## WebSocket event stream

Endpoint: `/api/v1/sessions/{session_id}/stream`.

After connecting, send the first message:

```json
{"type":"authenticate","player_token":"<session-scoped-token>"}
```

The server authenticates the token against the requested session and responds with a full `snapshot`, player context and the latest event sequence. It then sends committed journal events in ascending sequence order and a heartbeat every 15 seconds. Invalid credentials close the connection with code 4401; an unknown session closes with 4404.

The token is sent in the first WebSocket message rather than a URL query parameter. Browser clients store it in `sessionStorage`. The current implementation polls PostgreSQL every 750 ms. The journal remains the durable source of event order. On reconnect the server sends a new full snapshot; clients refresh state through HTTP rather than applying event payloads as local mutations.

See `docs/STAGE3_DESIGN.md` and ADR-006 for consistency, recovery and security details.

## Project workflow

Project lifecycle: `draft → submitted → approved/rejected → executing → completed/cancelled`. Submission reserves budget atomically. Domain leads approve their domains; a non-zero budget also requires finance approval. The author cannot approve their own proposal. Execution checks and consumes materials, reserves aggregate FTE, and requests priority-dispatched power. Progress advances only when the full energy request is served. Completion commissions typed results: infrastructure, recurring resource production, repairs and technology records.

## Persistence and validation

PostgreSQL stores scenario snapshots, session state, players, projects, approvals and append-only events. Alembic versions the schema. Period advancement and its events commit in one transaction. The GitHub Actions workflow applies migrations, runs backend tests, builds frontend, validates Compose and starts the web/API/database stack.
