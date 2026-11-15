"""Player membership, scoped permissions and project workflow endpoints."""
import hashlib
import secrets

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
    GameEventRecord, GameSessionRecord, PlayerRecord,
    ProjectApprovalRecord, ProjectRecord,
)

router = APIRouter(prefix="/api/v1")


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
        "resource_requirements": project.resource_requirements,
        "expected_result": project.expected_result, "version": project.version,
        "approvals": [{
            "player_id": item.player_id, "decision": item.decision,
            "comment": item.comment, "created_at": item.created_at,
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
    resource_requirements: dict = Field(default_factory=dict)
    expected_result: dict = Field(default_factory=dict)


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
    if ("domain_lead" in request.roles) != bool(request.responsibility_domains):
        raise HTTPException(status_code=422, detail="Only domain leads may have responsibility domains.")
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
            catalog = (session.scenario_snapshot or {}).get("resource_catalog", {})
            for resource_id, quantity in request.resource_requirements.items():
                if resource_id not in catalog:
                    raise HTTPException(status_code=422, detail=f"Unknown resource requirement: {resource_id}.")
                if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or quantity < 0:
                    raise HTTPException(status_code=422, detail=f"Invalid quantity for resource: {resource_id}.")
            project = ProjectRecord(
                session_id=session_id, author_player_id=actor.id,
                title=request.title.strip(), description=request.description.strip(),
                project_type=request.project_type, status="draft", domain_codes=domains,
                budget_credits=request.budget_credits, duration_periods=request.duration_periods,
                resource_requirements=request.resource_requirements,
                expected_result=request.expected_result, version=1,
            )
            if not project.title or not project.description:
                raise HTTPException(status_code=422, detail="Project title and description are required.")
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
            return {"projects": [serialize_project(item) for item in projects]}
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="Game storage is unavailable.") from error


@router.post("/sessions/{session_id}/projects/{project_id}/submit")
def submit_project(session_id: str, project_id: str, request: VersionRequest,
                   token: str | None = Header(default=None, alias="X-Player-Token")) -> dict:
    try:
        with SessionLocal.begin() as db:
            actor = authenticate(db, session_id, token)
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
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
            actor = authenticate(db, session_id, token)
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
            project = db.scalar(select(ProjectRecord).where(
                ProjectRecord.id == project_id, ProjectRecord.session_id == session_id
            ).with_for_update())
            if session is None or project is None:
                raise HTTPException(status_code=404, detail="Session or project not found.")
            if project.version != request.expected_version:
                raise HTTPException(status_code=409, detail="Project version is stale.")
            if project.status != "submitted":
                raise HTTPException(status_code=409, detail="Only submitted projects can be approved.")
            if project.author_player_id == actor.id:
                raise HTTPException(status_code=403, detail="Project authors cannot approve their own project.")
            actor_policy = policy(actor)
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
                decision=request.decision, comment=request.comment.strip(),
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
                    "player": policy(db.get(PlayerRecord, item.player_id)),
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
            actor = authenticate(db, session_id, token)
            session = db.scalar(select(GameSessionRecord).where(
                GameSessionRecord.id == session_id
            ).with_for_update())
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
            if float(session.state["budget_credits"]) + 1e-9 < project.budget_credits:
                raise HTTPException(status_code=409, detail="Reserved budget is no longer available.")
            session.state = dict(session.state)
            session.state["budget_credits"] = round(
                float(session.state["budget_credits"]) - project.budget_credits, 6
            )
            session.reserved_budget_credits = max(
                0.0, float(session.reserved_budget_credits) - project.budget_credits
            )
            project.status = "executing"
            project.version += 1
            append_event(db, session_id, "project.execution_started", {
                "project_id": project.id, "budget_spent": project.budget_credits,
                "budget_remaining": session.state["budget_credits"],
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
            project.status = "completed"
            project.version += 1
            append_event(db, session_id, "project.completed", {
                "project_id": project.id, "completed_by": actor.id,
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
