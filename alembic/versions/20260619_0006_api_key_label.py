"""Add API key label and update the default monitoring interval."""

from alembic import op
import sqlalchemy as sa

revision = "20260619_0006"
down_revision = "20260618_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "api_key_label",
                sa.String(length=120),
                nullable=False,
                server_default="Default",
            )
        )
    op.execute("UPDATE accounts SET interval_minutes = 5 WHERE interval_minutes = 1")


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("api_key_label")
