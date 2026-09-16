"""Persist independent Q&A records with immutable evidence snapshots."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = '0002_question_history'
down_revision = '0001_legacy_v2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'question_history',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('question', mysql.TEXT(), nullable=False),
        sa.Column('answer', mysql.LONGTEXT(), nullable=False),
        sa.Column('status', mysql.ENUM('ANSWERED', 'PARTIAL', 'REFUSED'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('elapsed_ms', sa.Integer(), nullable=False),
        sa.Column('model', mysql.JSON(), nullable=False),
        sa.Column('sources', mysql.JSON(), nullable=False),
        sa.Column('trace', mysql.JSON(), nullable=False),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_question_history_created', 'question_history', ['created_at', 'id'])


def downgrade():
    raise RuntimeError('Restore the complete backup to roll back; question history is never dropped by migrations')
