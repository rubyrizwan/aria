"""Add provider detection and discovered models."""

from alembic import op
import sqlalchemy as sa

revision = "20260618_0002"
down_revision = "20260618_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "provider_type",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(
            sa.Column(
                "models_json", sa.Text(), nullable=False, server_default="[]"
            )
        )
        batch.add_column(
            sa.Column("models_endpoint", sa.String(length=2048), nullable=True)
        )
    with op.batch_alter_table("check_results") as batch:
        batch.add_column(
            sa.Column(
                "provider_type",
                sa.String(length=20),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.add_column(
            sa.Column(
                "model_count", sa.Integer(), nullable=False, server_default="0"
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("check_results") as batch:
        batch.drop_column("model_count")
        batch.drop_column("provider_type")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("models_endpoint")
        batch.drop_column("models_json")
        batch.drop_column("provider_type")
