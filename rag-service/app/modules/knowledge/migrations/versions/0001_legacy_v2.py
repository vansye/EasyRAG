"""Baseline equivalent to the original Flyway V2, with historical SQL preserved."""

from pathlib import Path

from alembic import op


revision = '0001_legacy_v2'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    legacy = Path(__file__).resolve().parents[1] / 'legacy'
    for name in ('V1__init.sql', 'V2__fix_updated_at_and_unique_seq.sql'):
        for statement in legacy.joinpath(name).read_text(encoding='utf-8-sig').split(';'):
            if statement.strip():
                op.execute(statement)


def downgrade():
    raise RuntimeError('Restore the complete backup to roll back; business tables are never dropped by migrations')
