"""Add players, project workflow and session budget reservations.

Revision ID: 0003_stage2_projects
Revises: 0002_scenario_snapshot
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_stage2_projects"
down_revision = "0002_scenario_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("game_sessions", sa.Column("invite_code_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "game_sessions",
        sa.Column("reserved_budget_credits", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_unique_constraint(
        "uq_game_sessions_invite_code_hash", "game_sessions", ["invite_code_hash"]
    )
    op.create_table(
        "players",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("responsibility_domains", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_players_token_hash"),
    )
    op.create_index("ix_players_session_id", "players", ["session_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_player_id", sa.String(length=36), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("project_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("domain_codes", sa.JSON(), nullable=False),
        sa.Column("budget_credits", sa.Float(), nullable=False),
        sa.Column("duration_periods", sa.Integer(), nullable=False),
        sa.Column("resource_requirements", sa.JSON(), nullable=False),
        sa.Column("expected_result", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_session_id", "projects", ["session_id"])
    op.create_index("ix_projects_author_player_id", "projects", ["author_player_id"])
    op.create_table(
        "project_approvals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.String(length=36), sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "player_id", name="uq_project_approval_player"),
    )
    op.create_index("ix_project_approvals_project_id", "project_approvals", ["project_id"])
    op.create_index("ix_project_approvals_player_id", "project_approvals", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_project_approvals_player_id", table_name="project_approvals")
    op.drop_index("ix_project_approvals_project_id", table_name="project_approvals")
    op.drop_table("project_approvals")
    op.drop_index("ix_projects_author_player_id", table_name="projects")
    op.drop_index("ix_projects_session_id", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_players_session_id", table_name="players")
    op.drop_table("players")
    op.drop_constraint("uq_game_sessions_invite_code_hash", "game_sessions", type_="unique")
    op.drop_column("game_sessions", "reserved_budget_credits")
    op.drop_column("game_sessions", "invite_code_hash")
