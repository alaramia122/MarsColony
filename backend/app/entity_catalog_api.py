"""Session-scoped entity catalogue and human-reviewed custom definitions."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4
import base64
import binascii
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .entity_catalog import EntityCatalogError, load_entity_catalog, validate_entity, validate_entity_catalogue_resources
from .domain.projects import DOMAIN_CODES
from .models import GameEntityImageRecord, GameEntityRecord, GameEventRecord, GameSessionRecord
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
    image_data_url: str | None = Field(default=None, max_length=6_000_000)


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


def _sanitize_entity_image(data_url: str | None) -> tuple[bytes, str] | None:
    if not data_url:
        return None
    if not data_url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")):
        raise HTTPException(status_code=422, detail="Use a JPEG, PNG or WebP image.")
    try:
        encoded = data_url.split(",", 1)[1]
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise HTTPException(status_code=422, detail="Image data is not valid base64.") from error
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must not exceed 4 MiB.")
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(status_code=422, detail="Use a JPEG, PNG or WebP image.")
            if source.width > 12000 or source.height > 12000 or source.width * source.height > 40_000_000:
                raise HTTPException(status_code=413, detail="Image dimensions exceed the 40-megapixel limit.")
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
            output = BytesIO()
            image.save(output, format="WEBP", quality=82, method=6)
            return output.getvalue(), "image/webp"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(status_code=422, detail="Image file is damaged or cannot be decoded.") from error


def _image_data_url(image: GameEntityImageRecord | None) -> str | None:
    if image is None:
        return None
    encoded = base64.b64encode(image.image_data).decode("ascii")
    return f"data:{image.media_type};base64,{encoded}"


def _serialize_entity(record: GameEntityRecord, image: GameEntityImageRecord | None = None) -> dict:
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
        "image_data_url": _image_data_url(image),
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
            validate_entity_catalogue_resources(catalogue, resource_catalog)
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
            image_records = db.scalars(select(GameEntityImageRecord).where(
                GameEntityImageRecord.entity_id.in_([item.id for item in custom + own_pending + pending])
            )).all() if custom or own_pending or pending else []
            images_by_entity = {item.entity_id: item for item in image_records}
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
                        "image_data_url": _image_data_url(images_by_entity.get(item.id)),
                    }
                    for item in custom
                ],
                "my_pending_proposals": [_serialize_entity(item, images_by_entity.get(item.id)) for item in own_pending],
                "pending_proposals": [_serialize_entity(item, images_by_entity.get(item.id)) for item in pending],
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
            reserved_fields = {"id", "entity_type", "name", "description", "domain_codes"}
            if reserved_fields.intersection(definition):
                raise HTTPException(status_code=422, detail="Entity identity and responsibility fields must be supplied outside the technical definition.")
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
            sanitized_image = _sanitize_entity_image(request.image_data_url)
            if sanitized_image is not None:
                image_bytes, media_type = sanitized_image
                db.add(GameEntityImageRecord(
                    entity_id=record.id, media_type=media_type,
                    image_data=image_bytes, size_bytes=len(image_bytes),
                ))
            _append_event(db, session_id, "catalog.entity_proposed", {
                "entity_id": record.id, "entity_key": entity_key,
                "entity_type": record.entity_type, "domain_codes": domains,
                "author_player_id": actor.id,
            })
            image_record = db.get(GameEntityImageRecord, record.id)
            return _serialize_entity(record, image_record)
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
