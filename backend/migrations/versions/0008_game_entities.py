"""Add versioned, human-reviewed game entity registry.

Revision ID: 0008_game_entities
Revises: 0007_ai_proposal_dialogue
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_game_entities"
down_revision = "0007_ai_proposal_dialogue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_entities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36),
                  sa.ForeignKey("game_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("author_player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("entity_key", sa.String(length=64), nullable=False, index=True),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("domain_codes", sa.JSON(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False,
                  server_default="pending_review"),
        sa.Column("supersedes_id", sa.String(length=36),
                  sa.ForeignKey("game_entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by_player_id", sa.String(length=36),
                  sa.ForeignKey("players.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("game_entities")
