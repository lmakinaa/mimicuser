"""Widen runs.segment_key to match runs.audience.

The segment key is derived from the audience string — `SegmentSpec.key` only
lowercases and strips it — but the column was half the width of the audience it
comes from. A perfectly legal 121-300 character audience passed request
validation, resolved a persona (spending a model call), and then failed on
INSERT with StringDataRightTruncationError, surfacing as an unexplained 500.

Revision ID: 0004_widen_segment_key
Revises: 0003_persona_cache
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_widen_segment_key"
down_revision = "0003_persona_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "runs",
        "segment_key",
        existing_type=sa.String(120),
        type_=sa.String(300),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing again would fail on any row already storing a longer key, so
    # truncate first rather than let the migration die halfway.
    op.execute("UPDATE runs SET segment_key = left(segment_key, 120)")
    op.alter_column(
        "runs",
        "segment_key",
        existing_type=sa.String(300),
        type_=sa.String(120),
        existing_nullable=True,
    )
