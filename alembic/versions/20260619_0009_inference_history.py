"""Add append-only model inference history."""

from alembic import op
import sqlalchemy as sa

revision = "20260619_0009"
down_revision = "20260619_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_inference_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column(
            "api_key_label",
            sa.String(length=120),
            nullable=False,
            server_default="Default",
        ),
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
    )
    op.create_index(
        "ix_model_inference_history_account_id",
        "model_inference_history",
        ["account_id"],
    )
    op.create_index(
        "ix_model_inference_history_model_id",
        "model_inference_history",
        ["model_id"],
    )
    op.create_index(
        "ix_model_inference_history_status",
        "model_inference_history",
        ["status"],
    )
    op.create_index(
        "ix_model_inference_history_checked_at",
        "model_inference_history",
        ["checked_at"],
    )
    op.execute(
        """
        INSERT INTO model_inference_history (
            account_id, model_id, api_key_label, status, http_status,
            latency_ms, error_message, checked_at
        )
        SELECT
            results.account_id,
            results.model_id,
            COALESCE(accounts.api_key_label, 'Default'),
            results.status,
            results.http_status,
            results.latency_ms,
            results.error_message,
            results.checked_at
        FROM model_inference_results AS results
        JOIN accounts ON accounts.id = results.account_id
        """
    )


def downgrade() -> None:
    op.drop_table("model_inference_history")
