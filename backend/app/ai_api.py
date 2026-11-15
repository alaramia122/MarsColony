"""Human-supervised AI proposal endpoints.

LLM output is stored as a proposal. Creating a proposal never mutates game state;
a separate player action creates an ordinary draft project for the existing workflow.
"""
import math
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .ai_gateway import (
    ProjectProposal, ProviderError, build_provider_context, configured_provider,
)
from .db import SessionLocal
from .domain.projects import DOMAIN_CODES, PROJECT_TYPES, can_propose_project
from .models import (
    AIProposalRecord, GameEventRecord, GameSessionRecord, PlayerRecord, ProjectRecord,
)
from .projects_api import authenticate, policy

router = APIRouter(prefix="/api/v1")


class ProposalRequest(BaseModel):
    text: str = Field(min_length=5, max_length=8000)


def _append_event(db, session_id: str, event_type: str, payload: dict) -> None:
    sequence = db.scalar(select(func.max(GameEventRecord.sequence)).where(
        GameEventRecord.session_id == session_id
    ))
    db.add(GameEventRecord(
        session_id=session_id,
        sequence=0 if sequence is None else int(sequence) + 1,
        event_type=event_type,
        payload=payload,
    ))
    db.flush()


def _validate_proposal(proposal: ProjectProposal, session, actor: PlayerRecord) -> None:
    domains = sorted(set(proposal.domain_codes))
    if len(domains) != len(proposal.domain_codes) or not set(domains).issubset(DOMAIN_CODES):
        raise HTTPException(status_code=422, detail="AI proposal contains unknown or duplicate domains.")
    if proposal.project_type not in PROJECT_TYPES:
        raise HTTPException(status_code=422, detail="AI proposal contains an unsupported project type.")
    if not can_propose_project(policy(actor), domains):
        raise HTTPException(status_code=403, detail="Player has no authority to propose this project.")
    scenario = session.scenario_snapshot or {}
    catalog = scenario.get("resource_catalog", {})
    available_fte = float(scenario.get("population", {}).get("available_fte", 0))
    if proposal.labor_fte > available_fte:
        raise HTTPException(status_code=422, detail="AI proposal exceeds scenario workforce capacity.")
    if not math.isfinite(proposal.energy_kwh_per_period):
        raise HTTPException(status_code=422, detail="AI proposal has invalid energy demand.")
    for resource_id, quantity in proposal.resource_requirements.items():
        if resource_id not in catalog or not math.isfinite(float(quantity)) or quantity < 0:
            raise HTTPException(status_code=422, detail=f"AI proposal has an invalid resource requirement: {resource_id}.")
    effects = proposal.expected_result
    allowed = {
        "resource_outputs_per_period", "power_generation_kw_delta",
        "power_load_kw_delta", "infrastructure",
        "infrastructure_repairs", "technology_unlocks",
    }
    if not isinstance(effects, dict) or not set(effects).issubset(allowed):
        raise HTTPException(status_code=422, detail="AI proposal contains unsupported project effects.")
    for resource_id, quantity in effects.get("resource_outputs_per_period", {}).items():
        if resource_id not in catalog or not math.isfinite(float(quantity)) or quantity <= 0:
            raise HTTPException(status_code=422, detail=f"AI proposal has an invalid output resource: {resource_id}.")
    for key in ("power_generation_kw_delta", "power_load_kw_delta"):
        value = effects.get(key, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise HTTPException(status_code=422, detail=f"AI proposal has an invalid {key}.")
    infrastructure = effects.get("infrastructure", [])
    if not isinstance(infrastructure, list):
        raise HTTPException(status_code=422, detail="AI proposal infrastructure must be a list.")
    for item in infrastructure:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip() or not isinstance(item.get("kind"), str) or not item["kind"].strip() or not isinstance(item.get("properties", {}), dict):
            raise HTTPException(status_code=422, detail="AI proposal infrastructure entries require name, kind and properties.")
    repairs = effects.get("infrastructure_repairs", [])
    if not isinstance(repairs, list):
        raise HTTPException(status_code=422, detail="AI proposal repairs must be a list.")
    for item in repairs:
        delta = item.get("condition_delta") if isinstance(item, dict) else None
        if not isinstance(item, dict) or not isinstance(item.get("infrastructure_id"), str) or isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(float(delta)) or not 0 < float(delta) <= 1:
            raise HTTPException(status_code=422, detail="AI proposal contains an invalid infrastructure repair.")
    technologies = effects.get("technology_unlocks", [])
    if not isinstance(technologies, list):
        raise HTTPException(status_code=422, detail="AI proposal technology unlocks must be a list.")
    seen = set()
    for item in technologies:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("name"), str) or not item["name"].strip() or isinstance(item.get("level", 1), bool) or not isinstance(item.get("level", 1), int) or item.get("level", 1) < 1 or item.get("id") in seen:
            raise HTTPException(status_code=422, detail="AI proposal contains an invalid technology unlock.")
        seen.add(item["id"])


def _serialize(record: AIProposalRecord) -> dict:
    return {
        "id": record.id,
        "session_id": record.session_id,
        "author_player_id": record.author_player_id,
        "source_text": record.source_text,
        "proposal": record.proposal,
        "status": record.status,
        "created_project_id": record.created_project_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.post("/sessions/{session_id}/ai/proposals", status_code=201)
def create_ai_proposal(
    session_id: str,
    request: ProposalRequest,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal() as db:
            session = db.get(GameSessionRecord, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            context = build_provider_context(session, actor)
        provider = configured_provider()
        proposal = provider.propose_project(request.text.strip(), context)
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            _validate_proposal(proposal, session, actor)
            record = AIProposalRecord(
                session_id=session_id,
                author_player_id=actor.id,
                source_text=request.text.strip(),
                proposal=proposal.model_dump(),
                status="pending_review",
            )
            db.add(record)
            db.flush()
            _append_event(db, session_id, "ai.proposal_created", {
                "proposal_id": record.id, "author_player_id": actor.id,
                "status": record.status,
            })
            return _serialize(record)
    except ProviderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.get("/sessions/{session_id}/ai/proposals")
def list_ai_proposals(
    session_id: str,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal() as db:
            authenticate(db, session_id, token)
            records = db.scalars(select(AIProposalRecord).where(
                AIProposalRecord.session_id == session_id
            ).order_by(AIProposalRecord.created_at.desc())).all()
            return {"proposals": [_serialize(item) for item in records]}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/ai/proposals/{proposal_id}/create-project", status_code=201)
def create_project_from_ai_proposal(
    session_id: str,
    proposal_id: str,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            record = db.scalar(select(AIProposalRecord).where(
                AIProposalRecord.id == proposal_id,
                AIProposalRecord.session_id == session_id,
            ).with_for_update())
            if record is None:
                raise HTTPException(status_code=404, detail="AI proposal not found.")
            if record.status != "pending_review":
                raise HTTPException(status_code=409, detail="AI proposal has already been processed.")
            if actor.id != record.author_player_id and "colony_commander" not in (actor.roles or []):
                raise HTTPException(status_code=403, detail="Only the proposal author or colony commander can create its project draft.")
            proposal = ProjectProposal.model_validate(record.proposal)
            _validate_proposal(proposal, session, actor)
            project = ProjectRecord(
                session_id=session_id,
                author_player_id=actor.id,
                title=proposal.title.strip(),
                description=proposal.description.strip(),
                project_type=proposal.project_type,
                status="draft",
                domain_codes=sorted(set(proposal.domain_codes)),
                budget_credits=proposal.budget_credits,
                duration_periods=proposal.duration_periods,
                labor_fte=proposal.labor_fte,
                energy_kwh_per_period=proposal.energy_kwh_per_period,
                resource_requirements=proposal.resource_requirements,
                expected_result=proposal.expected_result,
                version=1,
            )
            db.add(project)
            db.flush()
            record.status = "project_created"
            record.created_project_id = project.id
            record.updated_at = datetime.now(timezone.utc)
            _append_event(db, session_id, "project.created_from_ai_proposal", {
                "proposal_id": record.id, "project_id": project.id,
                "author_player_id": actor.id,
            })
            return {
                "proposal": _serialize(record),
                "project": {
                    "id": project.id, "title": project.title,
                    "status": project.status, "version": project.version,
                    "domain_codes": project.domain_codes,
                },
            }
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error
