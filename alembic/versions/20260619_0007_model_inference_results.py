"""Add per-model inference test results."""

from alembic import op
import sqlalchemy as sa

revision = "20260619_0007"
down_revision = "20260619_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_inference_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "account_id",
            "model_id",
            name="uq_model_inference_account_model",
        ),
    )
    op.create_index(
        "ix_model_inference_results_account_id",
        "model_inference_results",
        ["account_id"],
    )
    op.create_index(
        "ix_model_inference_results_model_id",
        "model_inference_results",
        ["model_id"],
    )
    op.create_index(
        "ix_model_inference_results_status",
        "model_inference_results",
        ["status"],
    )
    op.create_index(
        "ix_model_inference_results_checked_at",
        "model_inference_results",
        ["checked_at"],
    )


def downgrade() -> None:
    op.drop_table("model_inference_results")
