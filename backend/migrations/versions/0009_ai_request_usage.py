"""Add per-player and per-session AI request quota tracking.

Revision ID: 0009_ai_request_usage
Revises: 0008_game_entities
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_ai_request_usage"
down_revision = "0008_game_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_request_usage",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "session_id", "player_id", "window_start",
            name="uq_ai_request_usage_session_player_window",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_request_usage")
