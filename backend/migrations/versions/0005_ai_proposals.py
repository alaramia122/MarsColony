"""Persist AI-generated project proposals.

Revision ID: 0005_ai_proposals
Revises: 0004_project_execution
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_ai_proposals"
down_revision = "0004_project_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_proposals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("author_player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_review"),
        sa.Column("created_project_id", sa.String(length=36),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ai_proposals")
