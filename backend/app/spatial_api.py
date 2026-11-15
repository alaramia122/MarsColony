"""Authenticated spatial registry and role-based infrastructure placement workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .domain.hex_grid import legacy_percent_to_axial, normalize_footprint
from .models import GameSessionRecord, MapPlacementRecord, ProjectRecord
from .projects_api import append_event, authenticate

router = APIRouter(prefix="/api/v1")
ACTIVE_STATUSES = {"pending_approval", "placed"}


class PlacementCreateRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=2, max_length=160)
    q: int = Field(ge=-100000, le=100000)
    r: int = Field(ge=-100000, le=100000)
    footprint: list[list[int]] = Field(default_factory=lambda: [[0, 0]], max_length=256)
    expected_map_version: int | None = Field(default=None, ge=0)
    metadata: dict = Field(default_factory=dict)


class PlacementDecisionRequest(BaseModel):
    decision: str
    domain_code: str = Field(min_length=1, max_length=40)
    comment: str = Field(default="", max_length=2000)
    command_order: bool = False
    justification: str = Field(default="", max_length=2000)


def _cells(record: MapPlacementRecord) -> set[tuple[int, int]]:
    return {(record.q + int(cell[0]), record.r + int(cell[1])) for cell in record.footprint}


def _serialize(record: MapPlacementRecord) -> dict:
    return {
        "id": record.id,
        "project_id": record.project_id,
        "object_type": record.object_type,
        "name": record.name,
        "q": record.q,
        "r": record.r,
        "footprint": record.footprint,
        "domain_codes": record.domain_codes,
        "required_approvals": record.required_approvals,
        "approvals": record.approvals,
        "status": record.status,
        "map_version": record.map_version,
        "metadata": record.metadata_json or {},
        "author_player_id": record.author_player_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


@router.get("/sessions/{session_id}/map")
def get_map(
    session_id: str,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        with SessionLocal() as db:
            authenticate(db, session_id, token)
            session = db.get(GameSessionRecord, session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Игровая сессия не найдена.")
            records = db.scalars(
                select(MapPlacementRecord)
                .where(MapPlacementRecord.session_id == session_id)
                .order_by(MapPlacementRecord.created_at)
            ).all()
            legacy = []
            for asset in (session.state or {}).get("infrastructure", []):
                properties = asset.get("properties", {})
                x = properties.get("x")
                y = properties.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    try:
                        q, r = legacy_percent_to_axial(float(x), float(y))
                    except ValueError:
                        continue
                    legacy.append({
                        "id": f"legacy:{asset.get('id', asset.get('name', 'object'))}",
                        "object_type": "infrastructure",
                        "name": asset.get("name", "Объект инфраструктуры"),
                        "q": q, "r": r, "footprint": [[0, 0]],
                        "status": asset.get("status", properties.get("status", "operational")),
                        "legacy": True,
                        "metadata": properties,
                    })
            state = dict(session.state or {})
            return {
                "map_version": int(state.get("map_version", 0)),
                "coordinate_system": "axial",
                "orientation": "pointy_top",
                "legacy_objects": legacy,
                "placements": [_serialize(item) for item in records],
            }
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Хранилище игры временно недоступно.") from error


@router.post("/sessions/{session_id}/map/placements", status_code=201)
def propose_placement(
    session_id: str,
    request: PlacementCreateRequest,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    try:
        footprint = normalize_footprint(
            (int(cell[0]), int(cell[1])) for cell in request.footprint
        )
    except (TypeError, ValueError, IndexError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    if [0, 0] not in footprint:
        raise HTTPException(status_code=422, detail="Площадка должна включать опорный гекс [0, 0].")

    try:
        with SessionLocal.begin() as db:
            session = db.scalar(
                select(GameSessionRecord).where(GameSessionRecord.id == session_id).with_for_update()
            )
            if session is None:
                raise HTTPException(status_code=404, detail="Игровая сессия не найдена.")
            actor = authenticate(db, session_id, token)
            project = db.get(ProjectRecord, request.project_id)
            if project is None or project.session_id != session_id:
                raise HTTPException(status_code=404, detail="Проект для размещения не найден.")
            if project.project_type not in {"construction", "infrastructure"}:
                raise HTTPException(status_code=422, detail="Для этого типа проекта согласование строительной площадки не требуется.")
            if project.status not in {"draft", "submitted", "approved"}:
                raise HTTPException(status_code=409, detail="Площадку нельзя менять после запуска или завершения проекта.")
            existing_site = db.scalar(select(MapPlacementRecord.id).where(
                MapPlacementRecord.session_id == session_id,
                MapPlacementRecord.project_id == project.id,
                MapPlacementRecord.status == "placed",
            ))
            if existing_site is not None:
                raise HTTPException(status_code=409, detail="Для проекта уже утверждена строительная площадка.")
            if actor.id != project.author_player_id and "colony_commander" not in (actor.roles or []) and not (
                "engineer" in (actor.roles or [])
                and bool(set(project.domain_codes or []).intersection(actor.responsibility_domains or []))
            ):
                raise HTTPException(status_code=403, detail="У вас нет полномочий предлагать площадку для этого проекта.")

            state = dict(session.state or {})
            map_version = int(state.get("map_version", 0))
            if request.expected_map_version is not None and request.expected_map_version != map_version:
                raise HTTPException(status_code=409, detail="Карта изменилась. Обновите её перед отправкой площадки.")

            occupied: set[tuple[int, int]] = set()
            records = db.scalars(select(MapPlacementRecord).where(
                MapPlacementRecord.session_id == session_id,
                MapPlacementRecord.status.in_(ACTIVE_STATUSES),
            )).all()
            for record in records:
                occupied.update(_cells(record))
            for asset in (state.get("infrastructure") or []):
                properties = asset.get("properties", {})
                x, y = properties.get("x"), properties.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    try:
                        occupied.add(legacy_percent_to_axial(float(x), float(y)))
                    except ValueError:
                        pass
            requested_cells = {(request.q + cell[0], request.r + cell[1]) for cell in footprint}
            if occupied.intersection(requested_cells):
                raise HTTPException(status_code=409, detail="Выбранная площадка пересекается с занятым гексом.")

            required = sorted(set(["construction"] + list(project.domain_codes or [])))
            record = MapPlacementRecord(
                id=str(uuid4()), session_id=session_id, project_id=project.id,
                author_player_id=actor.id, object_type="infrastructure",
                name=request.name.strip(), q=request.q, r=request.r,
                footprint=footprint, domain_codes=list(project.domain_codes or []),
                required_approvals=required, approvals=[], status="pending_approval",
                map_version=map_version,
                metadata_json={
                    "kind": next((
                        item.get("kind", "infrastructure")
                        for item in (project.expected_result or {}).get("infrastructure", [])
                        if item.get("name") == request.name.strip()
                    ), "infrastructure"),
                    "properties": next((
                        dict(item.get("properties", {}))
                        for item in (project.expected_result or {}).get("infrastructure", [])
                        if item.get("name") == request.name.strip()
                    ), {}),
                    "domain_codes": list(project.domain_codes or []),
                },
            )
            db.add(record)
            append_event(db, session_id, "map.placement_proposed", {
                "placement_id": record.id, "project_id": project.id,
                "name": record.name, "q": record.q, "r": record.r,
                "required_approvals": required, "map_version": map_version,
            })
            return _serialize(record)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Хранилище игры временно недоступно.") from error


@router.post("/sessions/{session_id}/map/placements/{placement_id}/decisions")
def decide_placement(
    session_id: str,
    placement_id: str,
    request: PlacementDecisionRequest,
    token: str | None = Header(default=None, alias="X-Player-Token"),
) -> dict:
    if request.decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="Решение должно быть «approve» или «reject».")
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(
                select(GameSessionRecord).where(GameSessionRecord.id == session_id).with_for_update()
            )
            if session is None:
                raise HTTPException(status_code=404, detail="Игровая сессия не найдена.")
            actor = authenticate(db, session_id, token)
            placement = db.scalar(select(MapPlacementRecord).where(
                MapPlacementRecord.id == placement_id,
                MapPlacementRecord.session_id == session_id,
            ).with_for_update())
            if placement is None:
                raise HTTPException(status_code=404, detail="Предложение площадки не найдено.")
            if placement.status != "pending_approval":
                raise HTTPException(status_code=409, detail="Предложение площадки уже обработано.")
            if actor.id == placement.author_player_id:
                raise HTTPException(status_code=403, detail="Автор предложения не может согласовать собственную площадку.")
            if request.domain_code not in (placement.required_approvals or []):
                raise HTTPException(status_code=422, detail="Это направление не участвует в согласовании площадки.")

            is_commander = "colony_commander" in (actor.roles or [])
            is_domain_lead = (
                "domain_lead" in (actor.roles or [])
                and request.domain_code in (actor.responsibility_domains or [])
            )
            if request.command_order:
                if not is_commander or len(request.justification.strip()) < 10:
                    raise HTTPException(status_code=403, detail="Командирское распоряжение требует полномочий командира и обоснования не короче 10 символов.")
            elif not is_domain_lead:
                raise HTTPException(status_code=403, detail="Решение по этому направлению может принять только его руководитель.")

            approvals = list(placement.approvals or [])
            if any(item.get("domain_code") == request.domain_code for item in approvals):
                raise HTTPException(status_code=409, detail="По этому направлению решение уже принято.")
            approval = {
                "player_id": actor.id, "domain_code": request.domain_code,
                "decision": request.decision, "comment": request.comment.strip(),
                "approval_mode": "commander_order" if request.command_order else "standard",
                "justification": request.justification.strip() if request.command_order else "",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            approvals.append(approval)
            placement.approvals = approvals
            if request.decision == "reject":
                placement.status = "rejected"
            elif set(item["domain_code"] for item in approvals if item["decision"] == "approve") >= set(placement.required_approvals or []):
                state = dict(session.state or {})
                current_version = int(state.get("map_version", 0))
                if placement.map_version != current_version:
                    occupied: set[tuple[int, int]] = set()
                    other_records = db.scalars(select(MapPlacementRecord).where(
                        MapPlacementRecord.session_id == session_id,
                        MapPlacementRecord.id != placement.id,
                        MapPlacementRecord.status.in_(ACTIVE_STATUSES),
                    )).all()
                    for other in other_records:
                        occupied.update(_cells(other))
                    for asset in (state.get("infrastructure") or []):
                        properties = asset.get("properties", {})
                        x, y = properties.get("x"), properties.get("y")
                        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                            try:
                                occupied.add(legacy_percent_to_axial(float(x), float(y)))
                            except ValueError:
                                pass
                    if occupied.intersection(_cells(placement)):
                        raise HTTPException(status_code=409, detail="Карта изменилась: площадка стала недоступна. Предложите новое место.")
                    placement.map_version = current_version
                alternatives = db.scalars(select(MapPlacementRecord).where(
                    MapPlacementRecord.session_id == session_id,
                    MapPlacementRecord.project_id == placement.project_id,
                    MapPlacementRecord.id != placement.id,
                    MapPlacementRecord.status == "pending_approval",
                )).all()
                for alternative in alternatives:
                    alternative.status = "superseded"
                placement.status = "placed"
                state["map_version"] = current_version + 1
                session.state = state
                placement.map_version = int(state["map_version"])
            append_event(db, session_id, "map.placement_decided", {
                "placement_id": placement.id, "domain_code": request.domain_code,
                "decision": request.decision, "status": placement.status,
                "approval_mode": approval["approval_mode"],
                "map_version": int((session.state or {}).get("map_version", 0)),
            })
            return _serialize(placement)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Хранилище игры временно недоступно.") from error
