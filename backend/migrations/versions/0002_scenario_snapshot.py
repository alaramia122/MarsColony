"""Persist the scenario definition used to create each game session.

Revision ID: 0002_scenario_snapshot
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_scenario_snapshot"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "game_sessions",
        sa.Column("scenario_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("game_sessions", "scenario_snapshot")
