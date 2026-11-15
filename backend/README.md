# MarsColony backend

## Local run

From the repository root:

~~~
docker compose up --build
~~~

The API is available at http://localhost:8000. Interactive OpenAPI documentation is at http://localhost:8000/docs; the machine-readable contract is at http://localhost:8000/openapi.json.

For local development without Docker, install backend/requirements.txt, set DATABASE_URL to a reachable PostgreSQL database, run Alembic migrations from the repository root, then start Uvicorn:

~~~
pip install -r backend/requirements.txt
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --reload
~~~

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
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/execute | Start an approved project |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/complete | Mark execution complete |
| POST | /api/v1/sessions/{session_id}/projects/{project_id}/cancel | Cancel a project before execution |

Create a session:

~~~
POST /api/v1/sessions
Content-Type: application/json

{"scenario_id":"ares-01"}
~~~

Session creation returns `owner_player_id`, `owner_token` and `join_code`. The owner token authenticates the creator as `colony_commander`; the invitation code allows additional participants to join. Store both values securely: they are returned only once. The database stores SHA-256 hashes rather than the raw credentials.

All participant endpoints use the `X-Player-Token` header. Tokens are scoped to one session. The commander assigns `domain_lead`, `finance_lead`, `engineer` or `player` roles. Domain leads and engineers receive one or more of the 16 responsibility domains; engineers may propose and execute scoped projects but do not replace domain-lead approval. The `colony_commander` role has session-wide authority.

Project lifecycle: `draft → submitted → approved/rejected → executing → completed/cancelled`. Submission reserves budget atomically. Domain leads approve the domains they own; a non-zero budget also requires finance approval. The author cannot approve their own proposal. Execution spends the reserved budget and consumes the declared material requirements. Active projects reserve aggregate FTE and request a priority-dispatched power load each period. Progress advances only when the full energy request is served. Completion requires the declared duration and commissions the validated result: construction creates infrastructure records, production adds recurring resource output, repair updates equipment condition, and research unlocks technology records. Added generation and operational load are included in later dispatch; output is constrained by served power and storage capacity. Project mutations require `expected_version` to reject stale commands.

Advance the period using the version observed by the client:

~~~
POST /api/v1/sessions/{session_id}/advance
Content-Type: application/json

{"expected_period":0}
~~~

The `X-Player-Token` header is required; only the colony commander may advance the period. The expected_period field is optional for compatibility. When supplied, the server compares it with the current period while holding a database row lock. A stale command receives HTTP 409; invalid scenario data receives 422; an unknown scenario or session receives 404; database failures receive 503.

The backend is the authoritative state owner. Clients submit domain commands; they do not send replacement game-state snapshots. Period advancement and its append-only event are committed in one PostgreSQL transaction.

Each session stores the exact scenario definition used at creation. Later edits to a scenario file affect new sessions; existing sessions continue with their stored snapshot.

## Scenario data contract

Scenario files are versioned JSON documents in data/scenarios/. The loader validates resource definitions, units, decimal precision, storage assignments and capacities, production recipes, battery limits and efficiencies, dispatch priorities, event periods and cross-references before a session is created.

Each resource definition declares a state key, unit, decimal precision and storage location. Storage locations declare their resource, physical location label and capacity. A null capacity represents a storage location without a modeled upper bound.

Production recipes declare input and output quantities per cycle, requested cycles per period, specific energy and an optional event multiplier key. Recipe changes are scenario-data changes; the deterministic process executor remains reusable.

Electrical loads are assigned to exactly one dispatch group. Groups declare priority, minimum stable power and an optional process whose production is constrained by allocated energy. The battery model tracks state of charge, reserve, maximum charge/discharge power and charge/discharge efficiency.

## Domain calculation modules

- backend/app/domain/engine.py — scenario-driven period calculation and campaign report.
- backend/app/domain/resources.py — deterministic material-process execution, input constraints, energy use and storage capacity.
- backend/app/domain/energy.py — priority-based electrical dispatch and battery operation.
- backend/app/scenarios.py — scenario loading and semantic validation.

The energy report separates generated energy, served load, battery discharge, charging input, conversion losses and unused process allocation. Run python tools/backend_campaign_report.py from the repository root to print the four-period acceptance report. Battery state is persisted as part of the session JSON state.

## Persistence and validation

The schema is versioned with Alembic. Scenario snapshots and session state are stored in PostgreSQL; each completed period appends a report event in the same transaction. The CI workflow applies migrations to PostgreSQL 16, runs domain/API/integration tests, validates scenario JSON and Compose configuration, builds and starts the complete stack, advances a session, restarts PostgreSQL and the API, and verifies that state and event history remain available.

The Compose stack starts PostgreSQL and the API. Database credentials can be overridden with POSTGRES_PASSWORD; the default is intended for local development only. Participant tokens and invitation codes provide session-level authentication and authorization for a trusted development LAN. HTTPS, token revocation/rotation and a broader identity lifecycle are required before exposing the service beyond that environment.

## Stage 2 boundary

The Stage 2 backend slice supplies session participants, scoped roles, project proposals, approval workflow, budget reservation and audit events. Project execution checks and consumes materials, allocates aggregate FTE, competes for power through dispatch, advances by periods and commissions infrastructure and recurring production effects. Individual skills, staffing rosters, detailed construction dependencies and project templates remain future refinements. WebSocket synchronization, frontend clients, AI Gateway and Alice integration remain later stages.
