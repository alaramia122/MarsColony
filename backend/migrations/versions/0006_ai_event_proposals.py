"""Persist human-reviewed AI game event proposals.

Revision ID: 0006_ai_event_proposals
Revises: 0005_ai_proposals
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_ai_event_proposals"
down_revision = "0005_ai_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_event_proposals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("author_player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("period_created", sa.Integer(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False,
                  server_default="pending_review"),
        sa.Column("published_sequence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ai_event_proposals")
