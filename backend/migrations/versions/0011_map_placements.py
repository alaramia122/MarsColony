"""Add persistent axial map placement proposals and approvals.

Revision ID: 0011_map_placements
Revises: 0010_entity_images
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_map_placements"
down_revision = "0010_entity_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "map_placements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("project_id", sa.String(length=36),
                  sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("author_player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("q", sa.Integer(), nullable=False),
        sa.Column("r", sa.Integer(), nullable=False),
        sa.Column("footprint", sa.JSON(), nullable=False),
        sa.Column("domain_codes", sa.JSON(), nullable=False),
        sa.Column("required_approvals", sa.JSON(), nullable=False),
        sa.Column("approvals", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("map_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_map_placements_session_id", "map_placements", ["session_id"])
    op.create_index("ix_map_placements_project_id", "map_placements", ["project_id"])
    op.create_index("ix_map_placements_author_player_id", "map_placements", ["author_player_id"])


def downgrade() -> None:
    op.drop_index("ix_map_placements_author_player_id", table_name="map_placements")
    op.drop_index("ix_map_placements_project_id", table_name="map_placements")
    op.drop_index("ix_map_placements_session_id", table_name="map_placements")
    op.drop_table("map_placements")
