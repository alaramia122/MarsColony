"""Add append-only movement history for mobile map entities.

Revision ID: 0012_map_position_history
Revises: 0011_map_placements
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_map_position_history"
down_revision = "0011_map_placements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "map_position_history",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("placement_id", sa.String(length=36),
                  sa.ForeignKey("map_placements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("from_q", sa.Integer(), nullable=True),
        sa.Column("from_r", sa.Integer(), nullable=True),
        sa.Column("to_q", sa.Integer(), nullable=False),
        sa.Column("to_r", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("map_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_map_position_history_session_id", "map_position_history", ["session_id"])
    op.create_index("ix_map_position_history_placement_id", "map_position_history", ["placement_id"])
    op.create_index("ix_map_position_history_player_id", "map_position_history", ["player_id"])


def downgrade() -> None:
    op.drop_index("ix_map_position_history_player_id", table_name="map_position_history")
    op.drop_index("ix_map_position_history_placement_id", table_name="map_position_history")
    op.drop_index("ix_map_position_history_session_id", table_name="map_position_history")
    op.drop_table("map_position_history")
