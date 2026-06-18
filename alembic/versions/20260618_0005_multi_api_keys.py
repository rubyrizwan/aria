"""Add multiple provider API keys and model access results."""

from alembic import op
import sqlalchemy as sa

revision = "20260618_0005"
down_revision = "20260618_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_provider_api_keys_account_id", "provider_api_keys", ["account_id"]
    )
    op.create_index("ix_provider_api_keys_enabled", "provider_api_keys", ["enabled"])
    op.execute(
        """
        INSERT INTO provider_api_keys
            (account_id, label, encrypted_api_key, enabled, created_at, updated_at)
        SELECT id, 'Default', encrypted_api_key, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM accounts
        """
    )

    op.create_table(
        "model_access_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "api_key_id",
            sa.Integer(),
            sa.ForeignKey("provider_api_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column("check_type", sa.String(length=20), nullable=False),
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
            "api_key_id",
            "model_id",
            "check_type",
            name="uq_model_access_key_model_type",
        ),
    )
    op.create_index(
        "ix_model_access_results_account_id", "model_access_results", ["account_id"]
    )
    op.create_index(
        "ix_model_access_results_api_key_id", "model_access_results", ["api_key_id"]
    )
    op.create_index(
        "ix_model_access_results_model_id", "model_access_results", ["model_id"]
    )
    op.create_index(
        "ix_model_access_results_check_type", "model_access_results", ["check_type"]
    )
    op.create_index(
        "ix_model_access_results_status", "model_access_results", ["status"]
    )
    op.create_index(
        "ix_model_access_results_checked_at", "model_access_results", ["checked_at"]
    )


def downgrade() -> None:
    op.drop_table("model_access_results")
    op.drop_table("provider_api_keys")
