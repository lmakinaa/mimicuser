"""persona cache

Revision ID: 0003_persona_cache
Revises: 0002_studies
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_persona_cache"
down_revision = "0002_studies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("audience_key", sa.String(300), nullable=False),
        sa.Column("audience", sa.String(300), nullable=False),
        sa.Column("resolver_version", sa.String(50), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("adopted_from", sa.String(60), nullable=True),
        sa.Column("persona", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("audience_key", "resolver_version", name="uq_persona_cache_key"),
    )
    op.create_index("ix_persona_cache_audience_key", "persona_cache", ["audience_key"])


def downgrade() -> None:
    op.drop_table("persona_cache")
