"""PCB percentage — second, equally-required eligibility dimension (director review)

Adds leads.pcb_percentage (nullable float). Tracked as real, persistent lead
state exactly like neet_score — see app/services/eligibility.py.

EligibilityFlag gains NEEDS_PCB (analogous to the existing NEEDS_CATEGORY) —
no migration needed for that: the enum columns in this project are
native_enum=False VARCHAR with no CHECK constraint (see 0001), so new Python
enum members work at runtime without a schema change.

Revision ID: 8b3f6a1c9d02
Revises: c9f1e05a7b62
Create Date: 2026-09-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '8b3f6a1c9d02'
down_revision: str | None = 'c9f1e05a7b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pcb_percentage', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('pcb_percentage')
