"""Add scalar market observations without turning them into OHLC candles."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    """Create a provenance-bearing metadata pointer for scalar Parquet series.

    Returns:
        No value; existing accounts, market candles and data files are untouched.
    """
    op.create_table(
        "market_observation_metadata",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.String(100), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("metric", sa.String(60), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("first_observed_at_ms", sa.BigInteger, nullable=False),
        sa.Column("last_observed_at_ms", sa.BigInteger, nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("source", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "metric"),
    )
    op.create_index("ix_market_observation_metadata_asset_id", "market_observation_metadata", ["asset_id"])


def downgrade():
    """Remove only the scalar-observation metadata table on explicit rollback.

    Returns:
        No value; this destructive downgrade is never run automatically.
    """
    op.drop_index("ix_market_observation_metadata_asset_id", table_name="market_observation_metadata")
    op.drop_table("market_observation_metadata")
