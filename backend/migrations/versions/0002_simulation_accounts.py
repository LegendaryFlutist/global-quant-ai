"""Add isolated research/simulation policy accounts without execution state."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    """Create the user-owned backtest configuration table.

    Returns:
        No return value; all monetary capital uses PostgreSQL NUMERIC.
    """
    op.create_table(
        "simulation_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("initial_equity", sa.Numeric(28, 8), nullable=False),
        sa.Column("base_currency", sa.String(8), nullable=False, server_default="USDT"),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_sha256", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_simulation_account_owner_name"),
    )
    op.create_index("ix_simulation_accounts_owner_user_id", "simulation_accounts", ["owner_user_id"])


def downgrade():
    """Drop only the newly introduced configuration table on manual rollback.

    Returns:
        No return value; operator must back up account configuration before rollback.
    """
    op.drop_index("ix_simulation_accounts_owner_user_id", table_name="simulation_accounts")
    op.drop_table("simulation_accounts")
