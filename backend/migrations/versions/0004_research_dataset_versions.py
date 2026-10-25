"""Add immutable research dataset versions without modifying old dataset pointers."""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    """Add source-specific version metadata and PostgreSQL mutation protection."""
    op.create_table(
        "dataset_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("asset_id", sa.String(100), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("provider_id", sa.String(50), sa.ForeignKey("providers.id"), nullable=False),
        sa.Column("interval", sa.String(16), nullable=False),
        sa.Column("price_type", sa.String(40), nullable=False),
        sa.Column("adjustment", sa.String(40), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("calendar_version", sa.String(100), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_path", sa.String(500), nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("first_open_ms", sa.BigInteger, nullable=False),
        sa.Column("last_close_ms", sa.BigInteger, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.JSON, nullable=False),
        sa.Column("quality", sa.JSON, nullable=False),
        sa.CheckConstraint("row_count > 0", name="ck_dataset_versions_rows"),
        sa.CheckConstraint("last_close_ms >= first_open_ms", name="ck_dataset_versions_times"),
    )
    op.create_index("ix_dataset_versions_asset_id", "dataset_versions", ["asset_id"])
    op.create_index("ix_dataset_versions_provider_id", "dataset_versions", ["provider_id"])
    op.execute("""CREATE FUNCTION reject_dataset_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'dataset versions are immutable; publish a new version';
        END $$""")
    op.execute("""CREATE TRIGGER dataset_versions_immutable BEFORE UPDATE OR DELETE
        ON dataset_versions FOR EACH ROW EXECUTE FUNCTION reject_dataset_version_mutation()""")


def downgrade():
    """Drop only this metadata schema on an explicit rollback; retain data files."""
    op.drop_table("dataset_versions")
    op.execute("DROP FUNCTION reject_dataset_version_mutation()")
