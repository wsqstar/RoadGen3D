"""Add the tenant-scoped scene asset palette to projects."""

from alembic import op
import sqlalchemy as sa


revision = "0003_project_asset_palette"
down_revision = "0002_job_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "asset_palette" not in existing:
        op.add_column(
            "projects",
            sa.Column(
                "asset_palette",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{\"schemaVersion\":\"roadgen3d.asset-palette.v1\",\"assets\":[]}'"),
            ),
        )


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("projects")}
    if "asset_palette" in existing:
        op.drop_column("projects", "asset_palette")
