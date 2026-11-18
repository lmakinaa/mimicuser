"""studies, study reports, consensus findings

Revision ID: 0002_studies
Revises: 0001_baseline
Create Date: 2026-08-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_studies"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    op.create_table(
        "studies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("task", sa.Text(), nullable=True),
        # Segments as submitted plus the persona each resolved to — frozen, so a
        # study stays explicable after the catalogue changes underneath it.
        sa.Column("segments", JSONB, nullable=False, server_default="[]"),
        sa.Column("budget", JSONB, nullable=False, server_default="{}"),
        sa.Column("models", JSONB, nullable=False, server_default="{}"),
        sa.Column("prompt_versions", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_studies_project_id", "studies", ["project_id"])
    op.create_index("ix_studies_status", "studies", ["status"])
    op.create_index("ix_studies_created_at", "studies", ["created_at"])

    # Nullable: every existing run stays valid, and a run outside a study
    # remains a first-class result.
    op.add_column("runs", sa.Column("study_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_runs_study_id", "runs", "studies", ["study_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_runs_study_id", "runs", ["study_id"])

    op.create_table(
        "study_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "study_id", UUID,
            sa.ForeignKey("studies.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("aggregator_version", sa.String(50), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("analysed_count", sa.Integer(), nullable=False),
        sa.Column("would_continue_rate", sa.Float(), nullable=False),
        sa.Column("purchase_intent", JSONB, nullable=False, server_default="{}"),
        sa.Column("retention_intent", JSONB, nullable=False, server_default="{}"),
        sa.Column("segments", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "consensus_findings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "report_id", UUID,
            sa.ForeignKey("study_reports.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("dimension", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
        # Computed from source_finding_ids, never taken from model output.
        sa.Column("agreement_count", sa.Integer(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("segments", JSONB, nullable=False, server_default="[]"),
        sa.Column("severity_spread", JSONB, nullable=False, server_default="[]"),
        sa.Column("source_finding_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("run_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("quotes", JSONB, nullable=False, server_default="[]"),
        sa.Column("likely_environmental", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_consensus_findings_report_id", "consensus_findings", ["report_id"])
    op.create_index("ix_consensus_findings_dimension", "consensus_findings", ["dimension"])
    op.create_index("ix_consensus_findings_severity", "consensus_findings", ["severity"])


def downgrade() -> None:
    op.drop_table("consensus_findings")
    op.drop_table("study_reports")
    op.drop_index("ix_runs_study_id", table_name="runs")
    op.drop_constraint("fk_runs_study_id", "runs", type_="foreignkey")
    op.drop_column("runs", "study_id")
    op.drop_table("studies")
