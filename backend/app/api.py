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


router = APIRouter(prefix="/api/v1")


class CreateSessionRequest(BaseModel):
    scenario_id: str = Field(default="ares-01", min_length=1, max_length=100)


class AdvancePeriodRequest(BaseModel):
    expected_period: int | None = Field(default=None, ge=0)




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
        "current_period": record.current_period,
        "reserved_budget_credits": record.reserved_budget_credits,
        "available_budget_credits": max(
            0.0, float(record.state["budget_credits"]) - float(record.reserved_budget_credits)
        ),
        "state": record.state,
        "created_at": record.created_at,
    }


@router.post("/sessions", status_code=201)
def create_session(request: CreateSessionRequest) -> dict:
    try:
        scenario = load_scenario(request.scenario_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ScenarioValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

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
                payload={"scenario_id": scenario["scenario_id"], "version": scenario["version"]},
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
                    "minimum_kw": 0.0,
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
