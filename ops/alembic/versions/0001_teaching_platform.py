"""Create the multi-tenant teaching platform schema."""

from alembic import op

from roadgen3d.teaching.database import Base
from roadgen3d.teaching import models  # noqa: F401

revision = "0001_teaching_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
