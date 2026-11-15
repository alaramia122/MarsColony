"""Persistent records for game sessions and append-only event history."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class GameSessionRecord(Base):
    __tablename__ = "game_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    scenario_id: Mapped[str] = mapped_column(String(100), nullable=False)
    scenario_version: Mapped[str] = mapped_column(String(40), nullable=False)
    scenario_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    invite_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    reserved_budget_credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_period: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    events: Mapped[list["GameEventRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="GameEventRecord.sequence",
    )


class GameEventRecord(Base):
    __tablename__ = "game_events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_game_event_session_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    session: Mapped[GameSessionRecord] = relationship(back_populates="events")


class PlayerRecord(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    responsibility_domains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    project_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    domain_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    budget_credits: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    labor_fte: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_kwh_per_period: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    progress_periods: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resource_requirements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProjectApprovalRecord(Base):
    __tablename__ = "project_approvals"
    __table_args__ = (
        UniqueConstraint("project_id", "player_id", name="uq_project_approval_player"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[str] = mapped_column(
        ForeignKey("players.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    authority_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
