"""Add isolated professional workspaces and administrator registration invites."""

from alembic import op
import sqlalchemy as sa


revision = "0004_personal_workspaces_and_registration_invites"
down_revision = "0003_project_asset_palette"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
    course_columns = {column["name"] for column in sa.inspect(bind).get_columns("courses")}
    if "scope" not in course_columns:
        op.add_column(
            "courses",
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="course"),
        )
        op.create_index("ix_courses_scope", "courses", ["scope"], unique=False)

    tables = set(sa.inspect(bind).get_table_names())
    if "registration_invites" not in tables:
        op.create_table(
            "registration_invites",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("code_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("created_by", sa.String(length=32), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("note", sa.String(length=240), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_registration_invites_code_hash", "registration_invites", ["code_hash"], unique=True)
        op.create_index("ix_registration_invites_created_by", "registration_invites", ["created_by"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "registration_invites" in tables:
        op.drop_table("registration_invites")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("courses")}
    if "scope" in columns:
        op.drop_index("ix_courses_scope", table_name="courses")
        op.drop_column("courses", "scope")
