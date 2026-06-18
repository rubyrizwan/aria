"""Add persistent application settings."""

from alembic import op
import sqlalchemy as sa

revision = "20260618_0003"
down_revision = "20260618_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('auto_monitoring_enabled', 'true', CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("app_settings")
