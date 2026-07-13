"""Persist structured teaching job progress."""

from alembic import op
import sqlalchemy as sa


revision = "0002_job_progress"
down_revision = "0001_teaching_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    if "stage" not in existing:
        op.add_column("jobs", sa.Column("stage", sa.String(length=64), nullable=False, server_default="queued"))
    if "message" not in existing:
        op.add_column("jobs", sa.Column("message", sa.Text(), nullable=False, server_default="Waiting for a worker."))
    if "detail" not in existing:
        op.add_column("jobs", sa.Column("detail", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    if "operations" not in existing:
        op.add_column("jobs", sa.Column("operations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("jobs")}
    for column in ("operations", "detail", "message", "stage"):
        if column in existing:
            op.drop_column("jobs", column)
