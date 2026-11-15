"""Add execution requirements and progress to colony projects.

Revision ID: 0004_project_execution
Revises: 0003_stage2_projects
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_project_execution"
down_revision = "0003_stage2_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("labor_fte", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("energy_kwh_per_period", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("progress_periods", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("started_period", sa.Integer(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("completed_period", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "completed_period")
    op.drop_column("projects", "started_period")
    op.drop_column("projects", "progress_periods")
    op.drop_column("projects", "energy_kwh_per_period")
    op.drop_column("projects", "labor_fte")
