"""Add revision history and clarification dialogue to AI proposals.

Revision ID: 0007_ai_proposal_dialogue
Revises: 0006_ai_event_proposals
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_ai_proposal_dialogue"
down_revision = "0006_ai_event_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ai_proposals",
        sa.Column("dialogue", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "ai_proposals",
        sa.Column("revision_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "ai_proposals",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("ai_proposals", "version")
    op.drop_column("ai_proposals", "revision_history")
    op.drop_column("ai_proposals", "dialogue")
