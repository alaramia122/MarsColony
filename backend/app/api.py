"""HTTP API for creating and advancing authoritative game sessions."""
import hashlib
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .domain.engine import DomainError, advance_period, initial_state
from .models import GameEventRecord, GameSessionRecord, PlayerRecord, ProjectRecord
from .scenarios import ScenarioValidationError, load_scenario
from .presets import apply_starter_preset, load_starter_presets, serialize_preset_catalogue


router = APIRouter(prefix="/api/v1")


class CreateSessionRequest(BaseModel):
    scenario_id: str = Field(default="ares-01", min_length=1, max_length=100)
    starter_preset_id: str = Field(default="balanced", min_length=1, max_length=100)


class AdvancePeriodRequest(BaseModel):
    expected_period: int | None = Field(default=None, ge=0)


class UpdateCampaignSettingsRequest(BaseModel):
    period_count: int | None = Field(default=None, ge=1, le=100)
    starting_resources: dict[str, float] | None = None
    starting_credits: float | None = Field(default=None, ge=0, le=1_000_000_000)




def _authenticate_player(db, session_id: str, token: str | None) -> PlayerRecord:
    if not token:
        raise HTTPException(status_code=401, detail="Player token is required.")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    player = db.scalar(select(PlayerRecord).where(
        PlayerRecord.session_id == session_id,
        PlayerRecord.token_hash == digest,
    ))
    if player is None:
        raise HTTPException(status_code=401, detail="Invalid player token for this session.")
    return player




def _next_event_sequence(db, session_id: str) -> int:
    current = db.scalar(
        select(func.max(GameEventRecord.sequence))
        .where(GameEventRecord.session_id == session_id)
    )
    return 0 if current is None else int(current) + 1


def serialize_session(record: GameSessionRecord) -> dict:
    return {
        "id": record.id,
        "scenario_id": record.scenario_id,
        "scenario_version": record.scenario_version,
        "starter_preset": (record.scenario_snapshot or {}).get("starter_preset"),
        "current_period": record.current_period,
        "settings": {
            "period_count": int((record.scenario_snapshot or {}).get("time", {}).get("period_count", 4)),
            "starting_resources": dict((record.scenario_snapshot or {}).get("stocks", {})),
            "starting_credits": float((record.scenario_snapshot or {}).get("finance", {}).get("starting_credits", 0)),
        },
        "reserved_budget_credits": record.reserved_budget_credits,
        "available_budget_credits": max(
            0.0, float(record.state["budget_credits"]) - float(record.reserved_budget_credits)
        ),
        "state": record.state,
        "created_at": record.created_at,
    }



@router.get("/scenarios/{scenario_id}/starter-presets")
def get_scenario_starter_presets(scenario_id: str) -> dict:
    """List the available initial infrastructure profiles for a scenario."""
    try:
        catalogue = load_starter_presets(scenario_id)
        return {
            "scenario_id": scenario_id,
            "version": catalogue.get("version"),
            "presets": serialize_preset_catalogue(catalogue),
        }
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(status_code=500, detail=f"Starter preset catalogue is invalid: {error}") from error


@router.post("/sessions", status_code=201)
def create_session(request: CreateSessionRequest) -> dict:
    try:
        scenario = load_scenario(request.scenario_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ScenarioValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    try:
        scenario = apply_starter_preset(scenario, request.starter_preset_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=422, detail=f"Unknown starter preset: {request.starter_preset_id}.") from error
    except ValueError as error:
        raise HTTPException(status_code=500, detail=f"Starter preset catalogue is invalid: {error}") from error

    join_code = secrets.token_urlsafe(24)
    owner_token = secrets.token_urlsafe(32)
    token_hash = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    record = GameSessionRecord(
        scenario_id=scenario["scenario_id"],
        scenario_version=scenario["version"],
        scenario_snapshot=scenario,
        invite_code_hash=token_hash(join_code),
        current_period=0,
        state=initial_state(scenario),
        reserved_budget_credits=0.0,
    )
    owner = PlayerRecord(
        session_id="", display_name="Командир колонии",
        token_hash=token_hash(owner_token),
        roles=["colony_commander"],
        responsibility_domains=[
            "geology", "industry", "energy", "life_support", "construction",
            "agriculture", "medicine", "social", "education", "science",
            "planetary", "transport", "administration", "safety",
            "communications", "external_relations",
        ],
    )
    try:
        with SessionLocal.begin() as db:
            db.add(record)
            db.flush()
            owner.session_id = record.id
            db.add(owner)
            db.add(GameEventRecord(
                session_id=record.id,
                sequence=0,
                event_type="session.created",
                payload={"scenario_id": scenario["scenario_id"], "version": scenario["version"], "starter_preset_id": request.starter_preset_id},
            ))
        result = serialize_session(record)
        result["owner_player_id"] = owner.id
        result["owner_token"] = owner_token
        result["join_code"] = join_code
        return result
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.get("/sessions/{session_id}")
def get_session(
    session_id: str,
    player_token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal() as db:
            player = _authenticate_player(db, session_id, player_token)
            record = db.get(GameSessionRecord, session_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            events = db.scalars(
                select(GameEventRecord)
                .where(GameEventRecord.session_id == session_id)
                .order_by(GameEventRecord.sequence)
            ).all()
            result = serialize_session(record)
            result["resource_catalog"] = (record.scenario_snapshot or {}).get("resource_catalog", {})
            result["events"] = [
                {
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at,
                }
                for event in events
            ]
            return result
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error



@router.patch("/sessions/{session_id}/settings")
def update_campaign_settings(
    session_id: str,
    request: UpdateCampaignSettingsRequest,
    player_token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    """Update campaign length and initial reserves before the first game period."""
    from math import isfinite

    try:
        with SessionLocal.begin() as db:
            record = db.scalar(
                select(GameSessionRecord)
                .where(GameSessionRecord.id == session_id)
                .with_for_update()
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            player = _authenticate_player(db, session_id, player_token)
            if "colony_commander" not in (player.roles or []):
                raise HTTPException(status_code=403, detail="Only the colony commander can change campaign settings.")
            if record.current_period != 0:
                raise HTTPException(status_code=409, detail="Campaign settings are locked after the first period starts.")
            project_count = db.scalar(
                select(func.count(ProjectRecord.id)).where(ProjectRecord.session_id == session_id)
            ) or 0
            if project_count:
                raise HTTPException(status_code=409, detail="Campaign settings are locked after the first project is created.")

            scenario = dict(record.scenario_snapshot or load_scenario(record.scenario_id))
            time_settings = dict(scenario["time"])
            stocks = dict(scenario["stocks"])
            finance = dict(scenario["finance"])
            catalog = scenario.get("resource_catalog", {})

            if request.period_count is not None:
                time_settings["period_count"] = request.period_count
                time_settings["total_sols"] = (
                    float(time_settings["period_sols"]) * request.period_count
                )

            if request.starting_resources is not None:
                for resource_id, raw_value in request.starting_resources.items():
                    if resource_id not in catalog:
                        raise HTTPException(status_code=422, detail=f"Unknown starting resource: {resource_id}.")
                    value = float(raw_value)
                    if not isfinite(value) or value < 0:
                        raise HTTPException(status_code=422, detail=f"Starting resource '{resource_id}' must be finite and non-negative.")
                    resource = catalog[resource_id]
                    storage_id = resource.get("storage_id")
                    storage = next((item for item in scenario["storage_locations"] if item["id"] == storage_id), None)
                    capacity = storage.get("capacity") if storage else None
                    if capacity is not None and value > float(capacity) + 1e-9:
                        raise HTTPException(status_code=422, detail=f"Starting resource '{resource_id}' exceeds storage capacity ({capacity} {resource.get('unit', '')}).")
                    state_key = resource["state_key"]
                    if state_key not in stocks:
                        raise HTTPException(status_code=422, detail=f"Scenario stock for '{resource_id}' is not configurable.")
                    stocks[state_key] = value

            if request.starting_credits is not None:
                value = float(request.starting_credits)
                if not isfinite(value) or value < 0:
                    raise HTTPException(status_code=422, detail="Starting credits must be finite and non-negative.")
                finance["starting_credits"] = value

            scenario["time"] = time_settings
            scenario["stocks"] = stocks
            scenario["finance"] = finance
            record.scenario_snapshot = scenario

            new_state = dict(record.state)
            for resource in catalog.values():
                state_key = resource["state_key"]
                if state_key in stocks:
                    new_state[state_key] = float(stocks[state_key])
            new_state["budget_credits"] = float(finance["starting_credits"])
            record.state = new_state

            sequence = _next_event_sequence(db, session_id)
            db.add(GameEventRecord(
                session_id=session_id,
                sequence=sequence,
                event_type="campaign.settings_updated",
                payload={
                    "period_count": int(time_settings["period_count"]),
                    "starting_resources": stocks,
                    "starting_credits": float(finance["starting_credits"]),
                },
            ))
            db.flush()
            return serialize_session(record)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error

@router.post("/sessions/{session_id}/advance")
def advance_game_session(
    session_id: str,
    request: AdvancePeriodRequest | None = None,
    player_token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal.begin() as db:
            record = db.scalar(
                select(GameSessionRecord)
                .where(GameSessionRecord.id == session_id)
                .with_for_update()
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            player = _authenticate_player(db, session_id, player_token)
            if "colony_commander" not in (player.roles or []):
                raise HTTPException(status_code=403, detail="Only the colony commander can advance the period.")
            if (
                request is not None
                and request.expected_period is not None
                and request.expected_period != record.current_period
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Session is at period {record.current_period}; "
                        f"expected period {request.expected_period}."
                    ),
                )
            scenario = record.scenario_snapshot or load_scenario(record.scenario_id)
            next_period = record.current_period + 1
            period_hours = (
                float(scenario["time"]["period_sols"])
                * float(scenario["time"]["hours_per_sol"])
            )
            active_projects = db.scalars(select(ProjectRecord).where(
                ProjectRecord.session_id == session_id,
                ProjectRecord.status == "executing",
                ProjectRecord.progress_periods < ProjectRecord.duration_periods,
            ).with_for_update()).all()
            allocated_fte = sum(float(item.labor_fte) for item in active_projects)
            available_fte = float(scenario.get("population", {}).get("available_fte", 0))
            if allocated_fte > available_fte + 1e-9:
                raise DomainError("Active project labor exceeds available workforce.")
            additional_loads = [
                {
                    "load_id": f"project:{item.id}",
                    "requested_kw": float(item.energy_kwh_per_period) / period_hours,
                    "priority": 4,
                    "minimum_kw": float(item.energy_kwh_per_period) / period_hours,
                }
                for item in active_projects
                if float(item.energy_kwh_per_period) > 0
            ]
            reserved_budget = float(record.reserved_budget_credits)
            engine_state = dict(record.state)
            engine_state["budget_credits"] = max(
                0.0, float(engine_state["budget_credits"]) - reserved_budget
            )
            new_state, report = advance_period(
                scenario, engine_state, next_period, additional_loads=additional_loads
            )
            new_state["budget_credits"] = round(
                float(new_state["budget_credits"]) + reserved_budget, 6
            )
            record.current_period = next_period
            record.state = new_state
            sequence = _next_event_sequence(db, record.id)
            db.add(GameEventRecord(
                session_id=record.id,
                sequence=sequence,
                event_type="period.completed",
                payload=report,
            ))
            sequence += 1
            project_execution = []
            for project in active_projects:
                load_id = f"project:{project.id}"
                delivered_kwh = sum(
                    float(load["allocated_kw"]) * float(interval["duration_hours"])
                    for interval in report["power_intervals"]
                    for load in interval["dispatch"]["loads"]
                    if load["load_id"] == load_id
                )
                required_kwh = float(project.energy_kwh_per_period)
                fully_powered = delivered_kwh + 1e-6 >= required_kwh
                if fully_powered:
                    project.progress_periods += 1
                    project.version += 1
                    event_type = "project.progressed"
                else:
                    event_type = "project.paused_power_shortfall"
                progress_event = {
                    "project_id": project.id,
                    "period": next_period,
                    "progress_periods": project.progress_periods,
                    "duration_periods": project.duration_periods,
                    "energy_required_kwh": required_kwh,
                    "energy_delivered_kwh": round(delivered_kwh, 6),
                    "fully_powered": fully_powered,
                    "ready_to_complete": project.progress_periods >= project.duration_periods,
                    "version": project.version,
                }
                db.add(GameEventRecord(
                    session_id=record.id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=progress_event,
                ))
                sequence += 1
                project_execution.append(progress_event)
            report["project_execution"] = project_execution
            db.flush()
            result = serialize_session(record)
            result["report"] = report
            return result
    except DomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error
