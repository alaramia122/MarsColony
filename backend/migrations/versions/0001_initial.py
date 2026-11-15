"""Create game session and append-only event tables.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scenario_id", sa.String(length=100), nullable=False),
        sa.Column("scenario_version", sa.String(length=40), nullable=False),
        sa.Column("current_period", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_game_events_session_id", "game_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_game_events_session_id", table_name="game_events")
    op.drop_table("game_events")
    op.drop_table("game_sessions")
