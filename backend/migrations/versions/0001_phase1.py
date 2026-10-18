"""Foundation schema. This revision keeps its own frozen table definitions."""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    """创建Phase1已交付的初始表、外键和唯一约束。

    Returns:
        无返回值；DDL与revision不因补充注释而改变。
    """
    op.create_table('users', sa.Column('id', sa.Integer, primary_key=True), sa.Column('username', sa.String(80), unique=True, nullable=False), sa.Column('password_hash', sa.String(300), nullable=False), sa.Column('role', sa.String(20), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('sessions', sa.Column('token_hash', sa.String(64), primary_key=True), sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False), sa.Column('csrf_token', sa.String(80), nullable=False), sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('security_audit_logs', sa.Column('id', sa.Integer, primary_key=True), sa.Column('actor', sa.String(80), nullable=False), sa.Column('action', sa.String(80), nullable=False), sa.Column('detail', sa.JSON, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('providers', sa.Column('id', sa.String(50), primary_key=True), sa.Column('name', sa.String(120), nullable=False), sa.Column('status', sa.String(40), nullable=False), sa.Column('detail', sa.JSON, nullable=False))
    op.create_table('assets', sa.Column('id', sa.String(100), primary_key=True), sa.Column('symbol', sa.String(40), nullable=False), sa.Column('name', sa.String(180), nullable=False), sa.Column('underlying_id', sa.String(100), nullable=False), sa.Column('asset_type', sa.String(50), nullable=False), sa.Column('venue', sa.String(40), nullable=False), sa.Column('priority', sa.Integer, nullable=False), sa.Column('verification', sa.String(40), nullable=False), sa.Column('specification', sa.JSON, nullable=False), sa.Column('verified_at', sa.DateTime(timezone=True)))
    op.create_index('ix_assets_symbol', 'assets', ['symbol'])
    op.create_index('ix_assets_underlying_id', 'assets', ['underlying_id'])
    op.create_table('asset_aliases', sa.Column('id', sa.Integer, primary_key=True), sa.Column('asset_id', sa.String(100), sa.ForeignKey('assets.id'), nullable=False), sa.Column('provider_id', sa.String(50), sa.ForeignKey('providers.id'), nullable=False), sa.Column('alias', sa.String(100), nullable=False), sa.UniqueConstraint('provider_id', 'alias'))
    op.create_table('sector_membership', sa.Column('asset_id', sa.String(100), sa.ForeignKey('assets.id'), primary_key=True), sa.Column('sector', sa.String(60), primary_key=True))
    op.create_table('market_data_metadata', sa.Column('id', sa.Integer, primary_key=True), sa.Column('asset_id', sa.String(100), sa.ForeignKey('assets.id'), nullable=False), sa.Column('interval', sa.String(10), nullable=False), sa.Column('path', sa.String(300), nullable=False), sa.Column('row_count', sa.Integer, nullable=False), sa.Column('first_open_ms', sa.BigInteger, nullable=False), sa.Column('last_close_ms', sa.BigInteger, nullable=False), sa.Column('sha256', sa.String(64), nullable=False), sa.Column('source', sa.JSON, nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint('asset_id', 'interval'))
    op.create_index('ix_market_data_metadata_asset_id','market_data_metadata',['asset_id'])
    op.create_table('scheduled_tasks', sa.Column('id', sa.String(36), primary_key=True), sa.Column('kind', sa.String(40), nullable=False), sa.Column('status', sa.String(30), nullable=False), sa.Column('parameters', sa.JSON, nullable=False), sa.Column('result', sa.JSON, nullable=False), sa.Column('attempts', sa.Integer, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('system_settings', sa.Column('key', sa.String(80), primary_key=True), sa.Column('value', sa.JSON, nullable=False))
    op.create_table('system_events', sa.Column('id', sa.Integer, primary_key=True), sa.Column('kind', sa.String(60), nullable=False), sa.Column('detail', sa.JSON, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    """按依赖逆序撤销Phase1表结构；仅供明确授权的迁移回退。

    Returns:
        无返回值；会删除表，恢复会话时禁止自动调用。
    """
    for name in ['system_events','system_settings','scheduled_tasks','market_data_metadata','sector_membership','asset_aliases','assets','providers','security_audit_logs','sessions','users']:
        op.drop_table(name)
