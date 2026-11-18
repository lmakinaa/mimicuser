"""baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("product_description", sa.Text(), nullable=True),
        sa.Column("default_audience", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "project_id", UUID,
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("product_url", sa.String(2048), nullable=False),
        sa.Column("audience", sa.String(300), nullable=False),
        sa.Column("task", sa.Text(), nullable=True),
        # The resolved persona, frozen: a run stays reproducible after the
        # catalogue changes underneath it.
        sa.Column("customer", JSONB, nullable=False),
        sa.Column("engine", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("budget", JSONB, nullable=False, server_default="{}"),
        sa.Column("prompt_versions", JSONB, nullable=False, server_default="{}"),
        sa.Column("segment_key", sa.String(120), nullable=True),
        sa.Column("stop_reason", sa.String(50), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])
    op.create_index("ix_runs_segment_key", "runs", ["segment_key"])

    # Append-only. Nothing in the codebase updates a row once written.
    op.create_table(
        "session_events",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("action", sa.String(60), nullable=True),
        sa.Column("target", sa.String(200), nullable=True),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("screenshot_key", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.UniqueConstraint("run_id", "seq", name="uq_session_events_run_seq"),
    )
    op.create_index("ix_session_events_run_seq", "session_events", ["run_id", "seq"])
    op.create_index("ix_session_events_type", "session_events", ["type"])

    op.create_table(
        "run_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id", UUID,
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True,
        ),
        sa.Column("evaluator_version", sa.String(50), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("first_impression", sa.Text(), nullable=False),
        sa.Column("what_they_thought_it_was", sa.Text(), nullable=False),
        sa.Column("task_completed", sa.Boolean(), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("would_continue", sa.Boolean(), nullable=False),
        sa.Column("purchase_intent", sa.String(10), nullable=False),
        sa.Column("retention_intent", sa.String(10), nullable=False),
        sa.Column("abandonment_reason", sa.Text(), nullable=True),
        sa.Column("customer_satisfaction", sa.Integer(), nullable=False),
        sa.Column("quotes", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "findings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "report_id", UUID,
            sa.ForeignKey("run_reports.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("dimension", sa.String(40), nullable=False),
        sa.Column("claim_type", sa.String(20), nullable=False),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=True),
    )
    op.create_index("ix_findings_report_id", "findings", ["report_id"])
    op.create_index("ix_findings_dimension", "findings", ["dimension"])
    op.create_index("ix_findings_severity", "findings", ["severity"])
    op.create_index("ix_findings_claim_type", "findings", ["claim_type"])

    op.create_table(
        "oauth_accounts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False, server_default="anthropic"),
        sa.Column("provider_account_id", sa.String(100), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("label", sa.String(200), nullable=False, server_default="Anthropic account"),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_oauth_accounts_provider_account",
        "oauth_accounts",
        ["provider", "provider_account_id"],
    )


def downgrade() -> None:
    op.drop_table("oauth_accounts")
    op.drop_table("findings")
    op.drop_table("run_reports")
    op.drop_table("session_events")
    op.drop_table("runs")
    op.drop_table("projects")
