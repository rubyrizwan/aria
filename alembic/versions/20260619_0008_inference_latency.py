"""Store the latest provider inference latency."""

from alembic import op
import sqlalchemy as sa

revision = "20260619_0008"
down_revision = "20260619_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("last_inference_latency_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "last_inference_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "last_inference_at")
    op.drop_column("accounts", "last_inference_latency_ms")
