"""HTTP API for creating and advancing authoritative game sessions."""
import hashlib
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .domain.engine import DomainError, advance_period, initial_state
from .models import GameEventRecord, GameSessionRecord, PlayerRecord
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
            player = _authenticate_player(db, session_id, player_token)
            if "colony_commander" not in (player.roles or []):
                raise HTTPException(status_code=403, detail="Only the colony commander can advance the period.")
            record = db.scalar(
                select(GameSessionRecord)
                .where(GameSessionRecord.id == session_id)
                .with_for_update()
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
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
            new_state, report = advance_period(scenario, record.state, next_period)
            record.current_period = next_period
            record.state = new_state
            db.add(GameEventRecord(
                session_id=record.id,
                sequence=_next_event_sequence(db, record.id),
                event_type="period.completed",
                payload=report,
            ))
            db.flush()
            result = serialize_session(record)
            result["report"] = report
            return result
    except DomainError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error
