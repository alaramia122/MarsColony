"""Store sanitized player-uploaded entity reference images.

Revision ID: 0010_entity_images
Revises: 0009_ai_request_usage
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_entity_images"
down_revision = "0009_ai_request_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_entity_images",
        sa.Column(
            "entity_id",
            sa.String(length=36),
            sa.ForeignKey("game_entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("media_type", sa.String(length=40), nullable=False),
        sa.Column("image_data", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("game_entity_images")
