"""Add product specification and alias timelines without altering legacy asset bindings."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    """Create additive, append-only version tables with exact native-unit numeric rules."""
    op.create_table("instrument_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("asset_id", sa.String(100), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("descriptor", sa.JSON, nullable=False),
        sa.UniqueConstraint("asset_id", "recorded_at", name="uq_instrument_recording"))
    op.create_index("ix_instrument_versions_asset_id", "instrument_versions", ["asset_id"])
    op.create_table("instrument_spec_periods",
        sa.Column("version_id", sa.String(64), sa.ForeignKey("instrument_versions.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("lot_size", sa.Numeric(38, 18)), sa.Column("tick_size", sa.Numeric(38, 18)),
        sa.Column("multiplier", sa.Numeric(38, 18)), sa.Column("min_notional", sa.Numeric(38, 18)),
        sa.Column("rules", sa.JSON, nullable=False),
        sa.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_instrument_period_bounds"),
        sa.CheckConstraint("lot_size IS NULL OR lot_size > 0", name="ck_instrument_lot"),
        sa.CheckConstraint("tick_size IS NULL OR tick_size > 0", name="ck_instrument_tick"),
        sa.CheckConstraint("multiplier IS NULL OR multiplier > 0", name="ck_instrument_multiplier"),
        sa.CheckConstraint("min_notional IS NULL OR min_notional >= 0", name="ck_instrument_notional"))
    op.create_table("instrument_alias_periods",
        sa.Column("version_id", sa.String(64), sa.ForeignKey("instrument_versions.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("provider_id", sa.String(50), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.CheckConstraint("effective_to IS NULL OR effective_to > effective_from", name="ck_instrument_alias_bounds"))
    op.create_index("ix_instrument_alias_periods_provider_id", "instrument_alias_periods", ["provider_id"])
    op.create_index("ix_instrument_alias_periods_alias", "instrument_alias_periods", ["alias"])
    op.execute("""CREATE FUNCTION reject_instrument_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'instrument timelines are immutable; publish a new version';
        END $$""")
    for table in ("instrument_versions", "instrument_spec_periods", "instrument_alias_periods"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_instrument_version_mutation()")


def downgrade():
    """Remove only this metadata on explicit downgrade; never touch prices or old Asset rows."""
    for table in ("instrument_alias_periods", "instrument_spec_periods", "instrument_versions"):
        op.drop_table(table)
    op.execute("DROP FUNCTION reject_instrument_version_mutation()")
