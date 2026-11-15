"""Session-scoped entity catalogue and human-reviewed custom definitions."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .entity_catalog import EntityCatalogError, load_entity_catalog, validate_entity
from .domain.projects import DOMAIN_CODES
from .models import GameEntityRecord, GameEventRecord, GameSessionRecord
from .projects_api import authenticate

router = APIRouter(prefix="/api/v1")


class EntityProposalRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=1, max_length=5000)
    domain_codes: list[str] = Field(min_length=1, max_length=16)
    definition: dict = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    source_text: str = Field(default="", max_length=8000)


class EntityApprovalRequest(BaseModel):
    comment: str = Field(default="", max_length=2000)


def _append_event(db, session_id: str, event_type: str, payload: dict) -> None:
    last = db.scalar(select(func.max(GameEventRecord.sequence)).where(
        GameEventRecord.session_id == session_id
    ))
    db.add(GameEventRecord(
        session_id=session_id,
        sequence=0 if last is None else int(last) + 1,
        event_type=event_type,
        payload=payload,
    ))
    db.flush()


def _serialize_entity(record: GameEntityRecord) -> dict:
    return {
        "id": record.id,
        "catalog_id": record.entity_key,
        "entity_type": record.entity_type,
        "name": record.name,
        "description": record.description,
        "domain_codes": record.domain_codes or [],
        "definition": record.definition or {},
        "assumptions": record.assumptions or [],
        "risks": record.risks or [],
        "version": record.version,
        "status": record.status,
        "author_player_id": record.author_player_id,
        "approved_by_player_id": record.approved_by_player_id,
    }


@router.get("/sessions/{session_id}/entity-catalog")
def get_entity_catalog(
    session_id: str,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal() as db:
            actor = authenticate(db, session_id, token)
            session = db.get(GameSessionRecord, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            catalogue = load_entity_catalog(session.scenario_id)
            resource_catalog = (session.scenario_snapshot or {}).get("resource_catalog", {})
            for entity in catalogue["entities"]:
                validate_entity(entity, resource_catalog, session.scenario_id)
            custom = db.scalars(select(GameEntityRecord).where(
                GameEntityRecord.session_id == session_id,
                GameEntityRecord.status == "approved",
            ).order_by(GameEntityRecord.name)).all()
            own_pending = db.scalars(select(GameEntityRecord).where(
                GameEntityRecord.session_id == session_id,
                GameEntityRecord.status == "pending_review",
                GameEntityRecord.author_player_id == actor.id,
            ).order_by(GameEntityRecord.created_at.desc())).all()
            pending = db.scalars(select(GameEntityRecord).where(
                GameEntityRecord.session_id == session_id,
                GameEntityRecord.status == "pending_review",
            ).order_by(GameEntityRecord.created_at)).all()
            return {
                "scenario_id": session.scenario_id,
                "version": catalogue.get("version"),
                "entities": catalogue["entities"] + [
                    {
                        "id": item.entity_key,
                        "entity_type": item.entity_type,
                        "name": item.name,
                        "description": item.description,
                        "domain_codes": item.domain_codes or [],
                        **(item.definition or {}),
                        "custom": True,
                        "record_id": item.id,
                    }
                    for item in custom
                ],
                "my_pending_proposals": [_serialize_entity(item) for item in own_pending],
                "pending_proposals": [_serialize_entity(item) for item in pending],
            }
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except EntityCatalogError as error:
        raise HTTPException(status_code=500, detail=f"Entity catalogue is invalid: {error}") from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/entity-catalog/proposals", status_code=201)
def propose_catalog_entity(
    session_id: str,
    request: EntityProposalRequest,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    domains = sorted(set(request.domain_codes))
    if len(domains) != len(request.domain_codes) or not set(domains).issubset(DOMAIN_CODES):
        raise HTTPException(status_code=422, detail="Entity domains must be unique valid responsibility domains.")
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            roles = set(actor.roles or [])
            if "colony_commander" not in roles and not (
                {"domain_lead", "engineer"}.intersection(roles)
                and set(domains).issubset(set(actor.responsibility_domains or []))
            ):
                raise HTTPException(status_code=403, detail="Creating a catalogue definition requires authority over every selected domain.")
            catalogue = load_entity_catalog(session.scenario_id)
            resource_catalog = (session.scenario_snapshot or {}).get("resource_catalog", {})
            definition = dict(request.definition)
            entity_key = "custom_" + uuid4().hex[:16]
            candidate = {
                "id": entity_key,
                "entity_type": request.entity_type,
                "name": request.name.strip(),
                "description": request.description.strip(),
                "domain_codes": domains,
                **definition,
            }
            validate_entity(candidate, resource_catalog, session.scenario_id)
            if any(item["id"] == entity_key for item in catalogue["entities"]):
                raise HTTPException(status_code=409, detail="Entity identifier collision; retry.")
            record = GameEntityRecord(
                session_id=session_id,
                author_player_id=actor.id,
                entity_key=entity_key,
                entity_type=request.entity_type,
                name=request.name.strip(),
                description=request.description.strip(),
                domain_codes=domains,
                definition=definition,
                assumptions=request.assumptions,
                risks=request.risks,
                source_text=request.source_text.strip(),
                version=1,
                status="pending_review",
            )
            db.add(record)
            db.flush()
            _append_event(db, session_id, "catalog.entity_proposed", {
                "entity_id": record.id, "entity_key": entity_key,
                "entity_type": record.entity_type, "domain_codes": domains,
                "author_player_id": actor.id,
            })
            return _serialize_entity(record)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except EntityCatalogError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/entity-catalog/proposals/{entity_id}/approve")
def approve_catalog_entity(
    session_id: str,
    entity_id: str,
    token: str | None = Header(default=None, alias="X-Player-Token"),
    request: EntityApprovalRequest | None = None,
) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            record = db.scalar(select(GameEntityRecord).where(
                GameEntityRecord.id == entity_id,
                GameEntityRecord.session_id == session_id,
            ).with_for_update())
            if record is None:
                raise HTTPException(status_code=404, detail="Entity proposal not found.")
            if record.status != "pending_review":
                raise HTTPException(status_code=409, detail="Only pending entity proposals can be approved.")
            roles = set(actor.roles or [])
            is_commander = "colony_commander" in roles
            is_own_commander_order = record.author_player_id == actor.id and is_commander
            if record.author_player_id == actor.id and not is_own_commander_order:
                raise HTTPException(status_code=403, detail="An entity author cannot approve their own definition.")
            if is_own_commander_order and (request is None or len(request.comment.strip()) < 10):
                raise HTTPException(status_code=422, detail="A commander's approval of their own entity requires a justification of at least 10 characters.")
            covers_domains = (
                "domain_lead" in roles
                and set(record.domain_codes or []).issubset(set(actor.responsibility_domains or []))
            )
            if not (is_commander or covers_domains):
                raise HTTPException(status_code=403, detail="Approval requires the commander or a lead for every affected domain.")
            resource_catalog = (session.scenario_snapshot or {}).get("resource_catalog", {})
            candidate = {
                "id": record.entity_key,
                "entity_type": record.entity_type,
                "name": record.name,
                "description": record.description,
                "domain_codes": record.domain_codes or [],
                **(record.definition or {}),
            }
            validate_entity(candidate, resource_catalog, session.scenario_id)
            record.status = "approved"
            record.approved_by_player_id = actor.id
            record.approved_at = datetime.now(timezone.utc)
            record.version += 1
            _append_event(db, session_id, "catalog.entity_approved", {
                "entity_id": record.id, "entity_key": record.entity_key,
                "approved_by_player_id": actor.id,
                "approval_mode": "commander_order" if is_own_commander_order else "standard",
                "comment": request.comment.strip() if request else "",
            })
            return _serialize_entity(record)
    except EntityCatalogError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error
