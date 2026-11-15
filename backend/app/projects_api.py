"""Player membership, scoped permissions and project workflow endpoints."""
import base64
import hashlib
from copy import deepcopy
import math
import secrets
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal
from .domain.projects import (
    DOMAIN_CODES, PROJECT_TYPES, ROLE_CODES, approvals_complete,
    can_manage_project, can_propose_project,
)
from .models import (
    GameEntityImageRecord, GameEntityRecord, GameEventRecord, GameSessionRecord, PlayerRecord,
    ProjectApprovalRecord, ProjectRecord,
)
from .entity_catalog import EntityCatalogError, load_entity_catalog, validate_entity

router = APIRouter(prefix="/api/v1")
MAX_SESSION_PLAYERS = 20


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(db, session_id: str, token: str | None) -> PlayerRecord:
    if not token:
        raise HTTPException(status_code=401, detail="Player token is required.")
    player = db.scalar(select(PlayerRecord).where(
        PlayerRecord.session_id == session_id,
        PlayerRecord.token_hash == token_hash(token),
    ))
    if player is None:
        raise HTTPException(status_code=401, detail="Invalid player token for this session.")
    return player


def policy(player: PlayerRecord) -> dict:
    return {"id": player.id, "roles": list(player.roles or []),
            "responsibility_domains": list(player.responsibility_domains or [])}


def append_event(db, session_id: str, event_type: str, payload: dict) -> None:
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


def serialize_project(project: ProjectRecord, approvals=None) -> dict:
    return {
        "id": project.id, "session_id": project.session_id,
        "author_player_id": project.author_player_id, "title": project.title,
        "description": project.description, "project_type": project.project_type,
        "status": project.status, "domain_codes": project.domain_codes,
        "budget_credits": project.budget_credits,
        "duration_periods": project.duration_periods,
        "labor_fte": project.labor_fte,
        "energy_kwh_per_period": project.energy_kwh_per_period,
        "progress_periods": project.progress_periods,
        "started_period": project.started_period,
        "completed_period": project.completed_period,
        "resource_requirements": project.resource_requirements,
        "expected_result": project.expected_result, "version": project.version,
        "approvals": [{
            "player_id": item.player_id, "decision": item.decision,
            "comment": item.comment,
            "approval_mode": (item.authority_snapshot or {}).get("approval_mode", "standard"),
            "created_at": item.created_at,
        } for item in (approvals or [])],
        "created_at": project.created_at, "updated_at": project.updated_at,
    }


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    invite_code: str = Field(min_length=20, max_length=100)


class RoleAssignment(BaseModel):
    roles: list[str] = Field(default_factory=list)
    responsibility_domains: list[str] = Field(default_factory=list)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=1, max_length=5000)
    project_type: str
    domain_codes: list[str] = Field(min_length=1, max_length=16)
    budget_credits: float = Field(default=0.0, ge=0.0, le=1_000_000_000)
    duration_periods: int = Field(ge=1, le=100)
    labor_fte: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    energy_kwh_per_period: float = Field(default=0.0, ge=0.0, le=1_000_000.0)
    resource_requirements: dict = Field(default_factory=dict)
    expected_result: dict = Field(default_factory=dict)
    catalog_entity_ids: list[str] = Field(default_factory=list, max_length=20)


class VersionRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ApprovalRequest(VersionRequest):
    decision: str
    comment: str = Field(default="", max_length=2000)


@router.post("/sessions/{session_id}/players", status_code=201)
def join_session(session_id: str, request: JoinRequest) -> dict:
    token = secrets.token_urlsafe(32)
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            if not session.invite_code_hash or not secrets.compare_digest(
                session.invite_code_hash, token_hash(request.invite_code)
            ):
                raise HTTPException(status_code=403, detail="Invalid session invitation code.")
            name = request.display_name.strip()
            if not name:
                raise HTTPException(status_code=422, detail="Display name cannot be empty.")
            player_count = db.scalar(select(func.count(PlayerRecord.id)).where(
                PlayerRecord.session_id == session_id
            ))
            if int(player_count or 0) >= MAX_SESSION_PLAYERS:
                raise HTTPException(
                    status_code=409,
                    detail=f"Session already has the maximum of {MAX_SESSION_PLAYERS} players.",
                )
            player = PlayerRecord(
                session_id=session_id, display_name=name, token_hash=token_hash(token),
                roles=["player"], responsibility_domains=[],
            )
            db.add(player)
            db.flush()
            append_event(db, session_id, "player.joined", {
                "player_id": player.id, "display_name": player.display_name,
            })
            return {"player_id": player.id, "display_name": player.display_name,
                    "roles": player.roles, "responsibility_domains": [],
                    "player_token": token}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.get("/sessions/{session_id}/players")
def list_players(session_id: str, token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal() as db:
            authenticate(db, session_id, token)
            players = db.scalars(select(PlayerRecord).where(
                PlayerRecord.session_id == session_id
            ).order_by(PlayerRecord.created_at)).all()
            return {"players": [{
                "id": item.id, "display_name": item.display_name,
                "roles": item.roles, "responsibility_domains": item.responsibility_domains,
            } for item in players]}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.put("/sessions/{session_id}/players/{target_id}/roles")
def assign_roles(session_id: str, target_id: str, request: RoleAssignment,
                 token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    if not set(request.roles).issubset(ROLE_CODES):
        raise HTTPException(status_code=422, detail="Unknown role code.")
    if not set(request.responsibility_domains).issubset(DOMAIN_CODES):
        raise HTTPException(status_code=422, detail="Unknown responsibility domain.")
    scoped_roles = {"domain_lead", "engineer"}.intersection(request.roles)
    if bool(scoped_roles) != bool(request.responsibility_domains):
        raise HTTPException(
            status_code=422,
            detail="Domain leads and engineers must have responsibility domains; other roles cannot own domains.",
        )
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            if "colony_commander" not in (actor.roles or []):
                raise HTTPException(status_code=403, detail="Only the colony commander can assign roles.")
            target = db.scalar(select(PlayerRecord).where(
                PlayerRecord.id == target_id, PlayerRecord.session_id == session_id
            ))
            if target is None:
                raise HTTPException(status_code=404, detail="Player not found in this session.")
            if (
                "colony_commander" in (target.roles or [])
                and "colony_commander" not in request.roles
            ):
                commanders = db.scalars(select(PlayerRecord).where(
                    PlayerRecord.session_id == session_id
                )).all()
                another_commander_exists = any(
                    item.id != target.id and "colony_commander" in (item.roles or [])
                    for item in commanders
                )
                if not another_commander_exists:
                    raise HTTPException(
                        status_code=409,
                        detail="At least one colony commander must remain in the session.",
                    )
            target.roles = sorted(set(request.roles))
            target.responsibility_domains = sorted(set(request.responsibility_domains))
            append_event(db, session_id, "player.roles_assigned", {
                "player_id": target.id, "roles": target.roles,
                "responsibility_domains": target.responsibility_domains,
                "assigned_by": actor.id,
            })
            return {"player_id": target.id, "roles": target.roles,
                    "responsibility_domains": target.responsibility_domains}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects", status_code=201)
def create_project(session_id: str, request: ProjectCreate,
                   token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    domains = sorted(set(request.domain_codes))
    if len(domains) != len(request.domain_codes) or not set(domains).issubset(DOMAIN_CODES):
        raise HTTPException(status_code=422, detail="Project domains must be unique valid domain codes.")
    if request.project_type not in PROJECT_TYPES:
        raise HTTPException(status_code=422, detail="Unknown project type.")
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            if not can_propose_project(policy(actor), domains):
                raise HTTPException(status_code=403, detail="Player has no authority to propose this project.")
            scenario = session.scenario_snapshot or {}
            catalog = scenario.get("resource_catalog", {})
            if request.labor_fte > float(scenario.get("population", {}).get("available_fte", 0)):
                raise HTTPException(status_code=422, detail="Project labor demand exceeds scenario workforce capacity.")
            if not math.isfinite(request.energy_kwh_per_period):
                raise HTTPException(status_code=422, detail="Project energy demand must be finite.")
            for resource_id, quantity in request.resource_requirements.items():
                if resource_id not in catalog:
                    raise HTTPException(status_code=422, detail=f"Unknown resource requirement: {resource_id}.")
                if (
                    isinstance(quantity, bool)
                    or not isinstance(quantity, (int, float))
                    or not math.isfinite(float(quantity))
                    or quantity < 0
                ):
                    raise HTTPException(status_code=422, detail=f"Invalid quantity for resource: {resource_id}.")
            expected_result = deepcopy(request.expected_result)
            available_entities = {}
            selected_ids = list(request.catalog_entity_ids)
            if len(selected_ids) != len(set(selected_ids)):
                raise HTTPException(status_code=422, detail="Catalogue entity selections must be unique.")
            if selected_ids:
                try:
                    entity_catalog = load_entity_catalog(session.scenario_id)
                except FileNotFoundError as error:
                    raise HTTPException(status_code=404, detail=str(error)) from error
                available_entities = {item["id"]: item for item in entity_catalog["entities"]}
                custom_records = db.scalars(select(GameEntityRecord).where(
                    GameEntityRecord.session_id == session_id,
                    GameEntityRecord.status == "approved",
                )).all()
                custom_images = db.scalars(select(GameEntityImageRecord).where(
                    GameEntityImageRecord.entity_id.in_([item.id for item in custom_records])
                )).all() if custom_records else []
                images_by_entity = {item.entity_id: item for item in custom_images}
                for item in custom_records:
                    image = images_by_entity.get(item.id)
                    image_data_url = (
                        f"data:{image.media_type};base64,{base64.b64encode(image.image_data).decode('ascii')}"
                        if image else None
                    )
                    available_entities[item.entity_key] = {
                        "id": item.entity_key, "entity_type": item.entity_type,
                        "name": item.name, "description": item.description,
                        "domain_codes": item.domain_codes or [],
                        "image_data_url": image_data_url,
                        **(item.definition or {}),
                    }
                total_catalog_budget = 0.0
                total_catalog_labor = 0.0
                total_catalog_energy = 0.0
                required_duration = 1
                catalog_requirements = {}
                infrastructure = list(expected_result.get("infrastructure", []))
                technologies = list(expected_result.get("technology_unlocks", []))
                procedures = list(expected_result.get("procedure_unlocks", []))
                for entity_id in selected_ids:
                    entity = available_entities.get(entity_id)
                    if entity is None:
                        raise HTTPException(status_code=422, detail=f"Unknown or unapproved catalogue entity: {entity_id}.")
                    try:
                        validate_entity(entity, catalog, session.scenario_id)
                    except EntityCatalogError as error:
                        raise HTTPException(status_code=422, detail=str(error)) from error
                    if not set(entity.get("domain_codes", [])).issubset(set(domains)):
                        raise HTTPException(status_code=422, detail=f"Project domains do not cover all domains of catalogue entity '{entity_id}'.")
                    build = entity.get("build", entity.get("research", {}))
                    total_catalog_budget += float(build.get("budget_credits", 0))
                    total_catalog_labor += float(build.get("labor_fte", 0))
                    total_catalog_energy += float(build.get("energy_kwh_per_period", 0))
                    required_duration = max(required_duration, int(build.get("duration_periods", 1)))
                    for resource_id, quantity in build.get("resource_requirements", {}).items():
                        catalog_requirements[resource_id] = catalog_requirements.get(resource_id, 0.0) + float(quantity)
                    if entity["entity_type"] == "technology":
                        technologies.append({
                            "id": entity_id, "name": entity["name"],
                            "description": entity["description"], "level": 1,
                            "domain_codes": list(entity.get("domain_codes", [])),
                            "catalog_entity_id": entity_id, "effects": entity.get("effects", {}),
                            "image_data_url": entity.get("image_data_url"),
                        })
                    elif entity["entity_type"] == "procedure":
                        procedures.append({
                            "id": entity_id, "name": entity["name"],
                            "description": entity["description"],
                            "domain_codes": list(entity.get("domain_codes", [])),
                            "catalog_entity_id": entity_id,
                            "image_data_url": entity.get("image_data_url"),
                        })
                    else:
                        infrastructure.append({
                            "name": entity["name"],
                            "kind": entity.get("kind", entity["entity_type"]),
                            "domain_codes": list(entity.get("domain_codes", [])),
                            "catalog_entity_id": entity_id,
                            "properties": {
                                "condition": 1.0,
                                "economic_effects": entity.get("operation", {}),
                                "catalog_entity_id": entity_id,
                                "image_data_url": entity.get("image_data_url"),
                            },
                        })
                if request.budget_credits + 1e-9 < total_catalog_budget:
                    raise HTTPException(status_code=422, detail=f"Project budget is below selected entities' minimum ({total_catalog_budget:g} credits).")
                if request.labor_fte + 1e-9 < total_catalog_labor:
                    raise HTTPException(status_code=422, detail=f"Project labor is below selected entities' minimum ({total_catalog_labor:g} FTE).")
                if request.energy_kwh_per_period + 1e-9 < total_catalog_energy:
                    raise HTTPException(status_code=422, detail=f"Project energy is below selected entities' minimum ({total_catalog_energy:g} kWh per period).")
                if request.duration_periods < required_duration:
                    raise HTTPException(status_code=422, detail=f"Project duration is below selected entities' minimum ({required_duration} periods).")
                for resource_id, quantity in catalog_requirements.items():
                    if float(request.resource_requirements.get(resource_id, 0)) + 1e-9 < quantity:
                        raise HTTPException(status_code=422, detail=f"Project does not reserve required '{resource_id}' for selected entities.")
                if infrastructure:
                    expected_result["infrastructure"] = infrastructure
                if technologies:
                    expected_result["technology_unlocks"] = technologies
                if procedures:
                    expected_result["procedure_unlocks"] = procedures

            project = ProjectRecord(
                session_id=session_id, author_player_id=actor.id,
                title=request.title.strip(), description=request.description.strip(),
                project_type=request.project_type, status="draft", domain_codes=domains,
                budget_credits=request.budget_credits, duration_periods=request.duration_periods,
                resource_requirements=request.resource_requirements,
                labor_fte=request.labor_fte,
                energy_kwh_per_period=request.energy_kwh_per_period,
                expected_result=expected_result, version=1,
            )
            if not project.title or not project.description:
                raise HTTPException(status_code=422, detail="Project title and description are required.")
            allowed_effects = {
                "resource_outputs_per_period", "power_generation_kw_delta",
                "power_load_kw_delta", "infrastructure",
                "infrastructure_repairs", "technology_unlocks", "procedure_unlocks"
            }
            if not set(expected_result).issubset(allowed_effects):
                raise HTTPException(status_code=422, detail="Project result contains an unsupported effect.")
            resource_outputs = expected_result.get("resource_outputs_per_period", {})
            if not isinstance(resource_outputs, dict):
                raise HTTPException(status_code=422, detail="Resource outputs must be an object.")
            for resource_id, quantity in resource_outputs.items():
                if resource_id not in catalog:
                    raise HTTPException(status_code=422, detail=f"Unknown output resource: {resource_id}.")
                if (
                    isinstance(quantity, bool) or not isinstance(quantity, (int, float))
                    or not math.isfinite(float(quantity)) or quantity <= 0
                ):
                    raise HTTPException(status_code=422, detail=f"Invalid output quantity for: {resource_id}.")
            generation_delta = expected_result.get("power_generation_kw_delta", 0.0)
            if (
                isinstance(generation_delta, bool)
                or not isinstance(generation_delta, (int, float))
                or not math.isfinite(float(generation_delta))
                or generation_delta < 0
            ):
                raise HTTPException(status_code=422, detail="Power generation effect must be finite and non-negative.")
            load_delta = expected_result.get("power_load_kw_delta", 0.0)
            if (
                isinstance(load_delta, bool)
                or not isinstance(load_delta, (int, float))
                or not math.isfinite(float(load_delta))
                or load_delta < 0
            ):
                raise HTTPException(status_code=422, detail="Power load effect must be finite and non-negative.")
            infrastructure = expected_result.get("infrastructure", [])
            if not isinstance(infrastructure, list):
                raise HTTPException(status_code=422, detail="Infrastructure result must be a list.")
            for item in infrastructure:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not item["name"].strip()
                    or not isinstance(item.get("kind"), str)
                    or not item["kind"].strip()
                    or not isinstance(item.get("properties", {}), dict)
                ):
                    raise HTTPException(status_code=422, detail="Each infrastructure item requires a name and kind.")
                properties = item.get("properties", {})
                item_domains = item.get("domain_codes", domains)
                if not isinstance(item_domains, list) or not set(item_domains).issubset(set(domains)):
                    raise HTTPException(status_code=422, detail="Infrastructure item domains must be covered by the project.")
                catalog_entity_id = item.get("catalog_entity_id") or properties.get("catalog_entity_id")
                if catalog_entity_id:
                    entity = available_entities.get(catalog_entity_id)
                    if (
                        catalog_entity_id not in selected_ids
                        or entity is None
                        or entity.get("entity_type") not in {"infrastructure", "equipment", "vehicle", "process"}
                    ):
                        raise HTTPException(status_code=422, detail="An infrastructure catalogue type must be selected and approved for this project.")
                    if item.get("kind") != entity.get("kind", entity["entity_type"]):
                        raise HTTPException(status_code=422, detail="Infrastructure kind does not match the selected catalogue definition.")
                    if set(item_domains) != set(entity.get("domain_codes", [])):
                        raise HTTPException(status_code=422, detail="Infrastructure domains must match the selected catalogue definition.")
                    if properties.get("economic_effects", {}) != entity.get("operation", {}):
                        raise HTTPException(status_code=422, detail="Infrastructure economic effects must exactly match the approved catalogue definition.")
                elif "economic_effects" in properties:
                    raise HTTPException(status_code=422, detail="Economic effects can only come from an approved infrastructure catalogue definition.")
                condition = properties.get("condition", 1.0)
                if (
                    isinstance(condition, bool)
                    or not isinstance(condition, (int, float))
                    or not math.isfinite(float(condition))
                    or not 0.0 <= float(condition) <= 1.0
                ):
                    raise HTTPException(status_code=422, detail="Infrastructure condition must be between zero and one.")
            repairs = expected_result.get("infrastructure_repairs", [])
            if not isinstance(repairs, list):
                raise HTTPException(status_code=422, detail="Infrastructure repairs must be a list.")
            for repair in repairs:
                delta = repair.get("condition_delta") if isinstance(repair, dict) else None
                if (
                    not isinstance(repair, dict)
                    or not isinstance(repair.get("infrastructure_id"), str)
                    or not repair["infrastructure_id"]
                    or isinstance(delta, bool)
                    or not isinstance(delta, (int, float))
                    or not math.isfinite(float(delta))
                    or not 0 < float(delta) <= 1
                ):
                    raise HTTPException(status_code=422, detail="Each repair requires an infrastructure ID and a condition delta in (0, 1].")
            technology_unlocks = expected_result.get("technology_unlocks", [])
            if not isinstance(technology_unlocks, list):
                raise HTTPException(status_code=422, detail="Technology unlocks must be a list.")
            technology_ids = set()
            for technology in technology_unlocks:
                if (
                    not isinstance(technology, dict)
                    or not isinstance(technology.get("id"), str)
                    or not technology["id"].strip()
                    or not isinstance(technology.get("name"), str)
                    or not technology["name"].strip()
                    or isinstance(technology.get("level", 1), bool)
                    or not isinstance(technology.get("level", 1), int)
                    or technology.get("level", 1) < 1
                    or not isinstance(technology.get("description", ""), str)
                    or technology.get("id") in technology_ids
                ):
                    raise HTTPException(status_code=422, detail="Each technology unlock requires a unique ID, name and positive integer level.")
                technology_ids.add(technology["id"])
                catalog_entity_id = technology.get("catalog_entity_id")
                technology_effects = technology.get("effects", {})
                if catalog_entity_id:
                    entity = available_entities.get(catalog_entity_id)
                    if (
                        catalog_entity_id not in selected_ids
                        or entity is None
                        or entity.get("entity_type") != "technology"
                        or technology.get("id") != catalog_entity_id
                        or set(technology.get("domain_codes", [])) != set(entity.get("domain_codes", []))
                        or technology_effects != entity.get("effects", {})
                    ):
                        raise HTTPException(status_code=422, detail="Technology effects must match an explicitly selected approved catalogue definition.")
                elif technology_effects:
                    raise HTTPException(status_code=422, detail="Non-catalogue technology unlocks cannot define economic modifiers.")
            procedure_unlocks = expected_result.get("procedure_unlocks", [])
            if not isinstance(procedure_unlocks, list):
                raise HTTPException(status_code=422, detail="Procedure unlocks must be a list.")
            procedure_ids = set()
            for procedure in procedure_unlocks:
                if (
                    not isinstance(procedure, dict)
                    or not isinstance(procedure.get("id"), str)
                    or not procedure["id"].strip()
                    or not isinstance(procedure.get("name"), str)
                    or not procedure["name"].strip()
                    or not isinstance(procedure.get("description", ""), str)
                    or procedure.get("id") in procedure_ids
                ):
                    raise HTTPException(status_code=422, detail="Each procedure unlock requires a unique ID and name.")
                procedure_ids.add(procedure["id"])
                catalog_entity_id = procedure.get("catalog_entity_id")
                entity = available_entities.get(catalog_entity_id)
                if (
                    catalog_entity_id not in selected_ids
                    or entity is None
                    or entity.get("entity_type") != "procedure"
                    or procedure.get("id") != catalog_entity_id
                    or set(procedure.get("domain_codes", [])) != set(entity.get("domain_codes", []))
                ):
                    raise HTTPException(status_code=422, detail="Procedure unlocks must reference an explicitly selected approved catalogue procedure.")
            db.add(project)
            db.flush()
            append_event(db, session_id, "project.created", {
                "project_id": project.id, "author_player_id": actor.id,
                "project_type": project.project_type, "domain_codes": domains,
            })
            return serialize_project(project)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.get("/sessions/{session_id}/projects")
def list_projects(session_id: str,
                  token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal() as db:
            authenticate(db, session_id, token)
            projects = db.scalars(select(ProjectRecord).where(
                ProjectRecord.session_id == session_id
            ).order_by(ProjectRecord.created_at)).all()
            return {"projects": [
                serialize_project(
                    item,
                    db.scalars(select(ProjectApprovalRecord).where(
                        ProjectApprovalRecord.project_id == item.id
                    )).all(),
                )
                for item in projects
            ]}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects/{project_id}/submit")
def submit_project(session_id: str, project_id: str, request: VersionRequest,
                   token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if session is None or project is None:
                raise HTTPException(status_code=404, detail="Session or project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status != "draft":
                raise HTTPException(status_code=409, detail="Only draft projects can be submitted.")
            if project.author_player_id != actor.id and "colony_commander" not in (actor.roles or []):
                raise HTTPException(status_code=403, detail="Only the author or colony commander can submit.")
            available = float(session.state["budget_credits"]) - float(session.reserved_budget_credits)
            if project.budget_credits > available + 1e-9:
                raise HTTPException(status_code=409, detail="Insufficient unreserved colony budget.")
            session.reserved_budget_credits += project.budget_credits
            project.status = "submitted"
            project.version += 1
            append_event(db, session_id, "project.submitted", {
                "project_id": project.id, "budget_reserved": project.budget_credits,
                "reserved_budget_total": session.reserved_budget_credits,
            })
            return serialize_project(project)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects/{project_id}/approvals")
def approve_project(session_id: str, project_id: str, request: ApprovalRequest,
                    token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    if request.decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="Decision must be approve or reject.")
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if session is None or project is None:
                raise HTTPException(status_code=404, detail="Session or project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status != "submitted":
                raise HTTPException(status_code=409, detail="Only submitted projects can be approved.")
            actor_policy = policy(actor)
            is_commander = "colony_commander" in actor_policy["roles"]
            is_commander_order = (
                project.author_player_id == actor.id
                and is_commander
                and request.decision == "approve"
            )
            if project.author_player_id == actor.id and not is_commander_order:
                raise HTTPException(
                    status_code=403,
                    detail="Project authors cannot approve their own project; a commander may issue a documented command order.",
                )
            if is_commander_order and len(request.comment.strip()) < 10:
                raise HTTPException(
                    status_code=422,
                    detail="A command order for the commander's own project requires a justification of at least 10 characters.",
                )
            domain_authority = (
                "colony_commander" in actor_policy["roles"]
                or ("domain_lead" in actor_policy["roles"]
                    and bool(set(project.domain_codes).intersection(actor_policy["responsibility_domains"])))
            )
            finance_authority = (
                "colony_commander" in actor_policy["roles"]
                or ("finance_lead" in actor_policy["roles"] and project.budget_credits > 0)
            )
            if not (domain_authority or finance_authority):
                raise HTTPException(status_code=403, detail="Player has no approval authority.")
            existing_vote = db.scalar(select(ProjectApprovalRecord).where(
                ProjectApprovalRecord.project_id == project.id,
                ProjectApprovalRecord.player_id == actor.id,
            ))
            if existing_vote is not None:
                raise HTTPException(status_code=409, detail="Player has already voted on this project.")
            approval = ProjectApprovalRecord(
                project_id=project.id, player_id=actor.id,
                decision=request.decision,
                authority_snapshot={
                    "roles": list(actor.roles or []),
                    "responsibility_domains": list(actor.responsibility_domains or []),
                    "approval_mode": "commander_order" if is_commander_order else "standard",
                },
                comment=request.comment.strip(),
            )
            db.add(approval)
            db.flush()
            if request.decision == "reject":
                project.status = "rejected"
                session.reserved_budget_credits = max(
                    0.0, float(session.reserved_budget_credits) - project.budget_credits
                )
                append_event(db, session_id, "project.budget_released", {
                    "project_id": project.id, "amount": project.budget_credits,
                })
            else:
                approvals = db.scalars(select(ProjectApprovalRecord).where(
                    ProjectApprovalRecord.project_id == project.id
                )).all()
                context = [{
                    "decision": item.decision,
                    "player": {
                        "id": item.player_id,
                        "roles": item.authority_snapshot["roles"],
                        "responsibility_domains": item.authority_snapshot["responsibility_domains"],
                    },
                } for item in approvals]
                if approvals_complete({
                    "domain_codes": project.domain_codes,
                    "budget_credits": project.budget_credits,
                }, context):
                    project.status = "approved"
            project.version += 1
            append_event(db, session_id, "project.approval_recorded", {
                "project_id": project.id, "player_id": actor.id,
                "decision": request.decision, "status": project.status,
                "approval_mode": "commander_order" if is_commander_order else "standard",
                "version": project.version,
            })
            saved = db.scalars(select(ProjectApprovalRecord).where(
                ProjectApprovalRecord.project_id == project.id
            )).all()
            return serialize_project(project, saved)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects/{project_id}/execute")
def execute_project(session_id: str, project_id: str, request: VersionRequest,
                    token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if session is None or project is None:
                raise HTTPException(status_code=404, detail="Session or project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status != "approved":
                raise HTTPException(status_code=409, detail="Project requires all approvals before execution.")
            if not can_manage_project(policy(actor), project.domain_codes):
                raise HTTPException(status_code=403, detail="Player has no authority to execute this project.")
            scenario = session.scenario_snapshot or {}
            available_fte = float(scenario.get("population", {}).get("available_fte", 0))
            active_projects = db.scalars(select(ProjectRecord).where(
                ProjectRecord.session_id == session_id,
                ProjectRecord.status == "executing",
                ProjectRecord.progress_periods < ProjectRecord.duration_periods,
            ).with_for_update()).all()
            technologies = session.state.get("technologies", [])
            def effective_labor(item):
                multiplier = 1.0
                project_domains = set(item.domain_codes or [])
                for technology in technologies:
                    if project_domains.intersection(technology.get("domain_codes", [])):
                        multiplier *= float(technology.get("effects", {}).get("labor_demand_multiplier", 1.0))
                return float(item.labor_fte) * multiplier
            allocated_fte = sum(effective_labor(item) for item in active_projects)
            if allocated_fte + effective_labor(project) > available_fte + 1e-9:
                raise HTTPException(status_code=409, detail="Insufficient unallocated workforce for this project.")
            catalog = scenario.get("resource_catalog", {})
            updated_state = dict(session.state)
            for resource_id, quantity in project.resource_requirements.items():
                resource = catalog[resource_id]
                state_key = resource["state_key"]
                available = float(updated_state[state_key])
                if available + 1e-9 < float(quantity):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient resource '{resource_id}' to start the project.",
                    )
                updated_state[state_key] = round(
                    available - float(quantity), int(resource["precision_digits"])
                )
            if (
                float(session.reserved_budget_credits) + 1e-9 < project.budget_credits
                or float(session.state["budget_credits"]) + 1e-9 < project.budget_credits
            ):
                raise HTTPException(status_code=409, detail="Project budget reservation is unavailable.")
            updated_state["budget_credits"] = round(
                float(updated_state["budget_credits"]) - project.budget_credits, 6
            )
            session.reserved_budget_credits = max(
                0.0, float(session.reserved_budget_credits) - project.budget_credits
            )
            session.state = updated_state
            project.status = "executing"
            project.progress_periods = 0
            project.started_period = session.current_period
            project.version += 1
            append_event(db, session_id, "project.materials_consumed", {
                "project_id": project.id, "resources": project.resource_requirements,
            })
            append_event(db, session_id, "project.execution_started", {
                "project_id": project.id, "budget_spent": project.budget_credits,
                "budget_remaining": updated_state["budget_credits"],
                "labor_fte": project.labor_fte,
                "energy_kwh_per_period": project.energy_kwh_per_period,
                "started_period": project.started_period,
            })
            return serialize_project(project)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects/{project_id}/complete")
def complete_project(session_id: str, project_id: str, request: VersionRequest,
                     token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status != "executing":
                raise HTTPException(status_code=409, detail="Only executing projects can be completed.")
            if not can_manage_project(policy(actor), project.domain_codes):
                raise HTTPException(status_code=403, detail="Player has no authority to complete this project.")
            if project.progress_periods < project.duration_periods:
                raise HTTPException(
                    status_code=409,
                    detail="Project has not completed its required execution periods.",
                )
            state = deepcopy(session.state)
            expected = project.expected_result or {}
            bonuses = dict(state.get("production_bonuses_per_period", {}))
            for resource_id, quantity in expected.get("resource_outputs_per_period", {}).items():
                bonuses[resource_id] = round(
                    float(bonuses.get(resource_id, 0.0)) + float(quantity), 6
                )
            state["production_bonuses_per_period"] = bonuses
            state["power_generation_kw_delta"] = round(
                float(state.get("power_generation_kw_delta", 0.0))
                + float(expected.get("power_generation_kw_delta", 0.0)), 6
            )
            state["power_load_kw_delta"] = round(
                float(state.get("power_load_kw_delta", 0.0))
                + float(expected.get("power_load_kw_delta", 0.0)), 6
            )
            infrastructure = list(state.get("infrastructure", []))
            commissioned = []
            for item in expected.get("infrastructure", []):
                asset = {
                    "id": str(uuid4()),
                    "name": item["name"].strip(),
                    "kind": item["kind"].strip(),
                    "domain_codes": list(item.get("domain_codes", project.domain_codes)),
                    "catalog_entity_id": item.get("catalog_entity_id"),
                    "commissioned_period": session.current_period,
                    "project_id": project.id,
                    "properties": item.get("properties", {}),
                    "condition": float(item.get("properties", {}).get("condition", 1.0)),
                }
                infrastructure.append(asset)
                commissioned.append(asset)
            state["infrastructure"] = infrastructure
            repaired_assets = []
            for repair in expected.get("infrastructure_repairs", []):
                asset = next(
                    (item for item in infrastructure
                     if item["id"] == repair["infrastructure_id"]),
                    None,
                )
                if asset is None:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Infrastructure asset '{repair['infrastructure_id']}' no longer exists.",
                    )
                old_condition = float(asset.get("condition", 1.0))
                asset["condition"] = min(
                    1.0, old_condition + float(repair["condition_delta"])
                )
                repaired_assets.append({
                    "infrastructure_id": asset["id"],
                    "condition_before": old_condition,
                    "condition_after": asset["condition"],
                })
            state["infrastructure"] = infrastructure
            technologies = list(state.get("technologies", []))
            existing_technology_ids = {item["id"] for item in technologies}
            unlocked_technologies = []
            for technology in expected.get("technology_unlocks", []):
                if technology["id"] in existing_technology_ids:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Technology '{technology['id']}' is already unlocked.",
                    )
                unlocked = {
                    "id": technology["id"],
                    "name": technology["name"].strip(),
                    "description": technology.get("description", "").strip(),
                    "level": technology.get("level", 1),
                    "domain_codes": list(technology.get("domain_codes", project.domain_codes)),
                    "catalog_entity_id": technology.get("catalog_entity_id"),
                    "effects": technology.get("effects", {}),
                    "unlocked_period": session.current_period,
                    "project_id": project.id,
                }
                technologies.append(unlocked)
                unlocked_technologies.append(unlocked)
                existing_technology_ids.add(technology["id"])
            state["technologies"] = technologies
            procedures = list(state.get("procedures", []))
            existing_procedure_ids = {item["id"] for item in procedures}
            unlocked_procedures = []
            for procedure in expected.get("procedure_unlocks", []):
                if procedure["id"] in existing_procedure_ids:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Procedure '{procedure['id']}' is already unlocked.",
                    )
                unlocked = {
                    "id": procedure["id"],
                    "name": procedure["name"].strip(),
                    "description": procedure.get("description", "").strip(),
                    "domain_codes": list(procedure.get("domain_codes", project.domain_codes)),
                    "catalog_entity_id": procedure.get("catalog_entity_id"),
                    "unlocked_period": session.current_period,
                    "project_id": project.id,
                }
                procedures.append(unlocked)
                unlocked_procedures.append(unlocked)
                existing_procedure_ids.add(procedure["id"])
            state["procedures"] = procedures
            session.state = state
            project.status = "completed"
            project.completed_period = session.current_period
            project.version += 1
            result_payload = {
                "project_id": project.id,
                "resource_outputs_per_period": expected.get("resource_outputs_per_period", {}),
                "power_generation_kw_delta": expected.get("power_generation_kw_delta", 0.0),
                "power_load_kw_delta": expected.get("power_load_kw_delta", 0.0),
                "infrastructure": commissioned,
                "infrastructure_repairs": repaired_assets,
                "technology_unlocks": unlocked_technologies,
                "procedure_unlocks": unlocked_procedures,
            }
            append_event(db, session_id, "project.result_applied", result_payload)
            if commissioned:
                append_event(db, session_id, "project.infrastructure_commissioned", {
                    "project_id": project.id, "assets": commissioned,
                })
            if unlocked_procedures:
                append_event(db, session_id, "project.procedures_unlocked", {
                    "project_id": project.id, "procedures": unlocked_procedures,
                })
            append_event(db, session_id, "project.completed", {
                "project_id": project.id, "completed_by": actor.id,
                "completed_period": project.completed_period,
            })
            return serialize_project(project)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error



@router.post("/sessions/{session_id}/projects/{project_id}/cancel")
def cancel_project(session_id: str, project_id: str, request: VersionRequest,
                   token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal.begin() as db:
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            if session is None:
                raise HTTPException(status_code=404, detail="Game session not found.")
            actor = authenticate(db, session_id, token)
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status not in {"draft", "submitted", "approved"}:
                raise HTTPException(status_code=409, detail="Project cannot be cancelled in its current status.")
            is_commander = "colony_commander" in (actor.roles or [])
            if actor.id != project.author_player_id and not is_commander:
                raise HTTPException(status_code=403, detail="Only the author or colony commander can cancel.")
            previous_status = project.status
            if previous_status in {"submitted", "approved"}:
                session.reserved_budget_credits = max(
                    0.0, float(session.reserved_budget_credits) - project.budget_credits
                )
                append_event(db, session_id, "project.budget_released", {
                    "project_id": project.id, "amount": project.budget_credits,
                })
            project.status = "cancelled"
            project.version += 1
            append_event(db, session_id, "project.cancelled", {
                "project_id": project.id, "cancelled_by": actor.id,
                "previous_status": previous_status,
            })
            return serialize_project(project)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error
