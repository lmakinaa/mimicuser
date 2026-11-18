"""SQLAlchemy models. All SQL lives under `repositories/`.

Artifacts store **keys only** — screenshots, video and traces live on the
StorageProvider. `session_events` is append-only; nothing in the codebase
updates a row once written, which is what makes the stream trustworthy as
evidence.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    product_url: Mapped[str] = mapped_column(String(2048))
    product_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_audience: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    runs: Mapped[list[RunRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    # studies.project_id has always been a real FK, but without this a project
    # could only reach its studies by hand-written query.
    studies: Mapped[list[StudyRow]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # Nullable: a run outside a study is still a first-class result.
    study_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )

    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(20))
    product_url: Mapped[str] = mapped_column(String(2048))
    audience: Mapped[str] = mapped_column(String(300))
    task: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The resolved persona, frozen. A run stays reproducible and explicable even
    # after the catalogue changes underneath it.
    customer: Mapped[dict] = mapped_column(JSONType)

    engine: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    budget: Mapped[dict] = mapped_column(JSONType, default=dict)
    prompt_versions: Mapped[dict] = mapped_column(JSONType, default=dict)

    # Nullable so a later aggregation layer can group runs into segments
    # without a migration.
    #
    # Must be at least as wide as `audience` above: the key is derived from the
    # audience string (SegmentSpec.key lowercases it, nothing more), so anything
    # narrower turns a legal 300-char audience into a 500 at INSERT time — which
    # is exactly what it did at 120.
    segment_key: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)

    stop_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[ProjectRow] = relationship(back_populates="runs")
    events: Mapped[list[SessionEventRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    report: Mapped[RunReportRow | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class SessionEventRow(Base):
    """Append-only. Never updated after insert."""

    __tablename__ = "session_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_session_events_run_seq"),
        Index("ix_session_events_run_seq", "run_id", "seq"),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE")
    )
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    type: Mapped[str] = mapped_column(String(40), index=True)

    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    action: Mapped[str | None] = mapped_column(String(60), nullable=True)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)

    run: Mapped[RunRow] = relationship(back_populates="events")


class RunReportRow(Base):
    __tablename__ = "run_reports"

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), unique=True
    )
    evaluator_version: Mapped[str] = mapped_column(String(50))

    summary: Mapped[str] = mapped_column(Text)
    first_impression: Mapped[str] = mapped_column(Text)
    what_they_thought_it_was: Mapped[str] = mapped_column(Text)
    task_completed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome: Mapped[str] = mapped_column(String(30))
    would_continue: Mapped[bool] = mapped_column(Boolean)
    purchase_intent: Mapped[str] = mapped_column(String(10))
    retention_intent: Mapped[str] = mapped_column(String(10))
    abandonment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_satisfaction: Mapped[int] = mapped_column(Integer)
    quotes: Mapped[list] = mapped_column(JSONType, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    run: Mapped[RunRow] = relationship(back_populates="report")
    findings: Mapped[list[FindingRow]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[UUID] = _uuid_pk()
    report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("run_reports.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)

    dimension: Mapped[str] = mapped_column(String(40), index=True)
    claim_type: Mapped[str] = mapped_column(String(20), index=True)
    observation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONType, default=list)
    impact: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    confidence: Mapped[str] = mapped_column(String(10))
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    report: Mapped[RunReportRow] = relationship(back_populates="findings")


class OAuthAccountRow(Base):
    """A linked Anthropic account. Tokens encrypted at rest."""

    __tablename__ = "oauth_accounts"
    # Covers the upsert lookup in OAuthAccountRepo.upsert.
    __table_args__ = (
        Index("ix_oauth_accounts_provider_account", "provider", "provider_account_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(30), default="anthropic")
    provider_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="Anthropic account")

    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StudyRow(Base):
    """One product, several audience segments, several customers each."""

    __tablename__ = "studies"

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)

    product_url: Mapped[str] = mapped_column(String(2048))
    task: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{audience, count, persona_id?}] as submitted, plus the persona each
    # resolved to — frozen so a study stays explicable after the catalogue moves.
    segments: Mapped[list] = mapped_column(JSONType, default=list)
    budget: Mapped[dict] = mapped_column(JSONType, default=dict)
    models: Mapped[dict] = mapped_column(JSONType, default=dict)
    prompt_versions: Mapped[dict] = mapped_column(JSONType, default=dict)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[ProjectRow] = relationship(back_populates="studies")

    report: Mapped[StudyReportRow | None] = relationship(
        back_populates="study", cascade="all, delete-orphan", uselist=False
    )


class StudyReportRow(Base):
    __tablename__ = "study_reports"

    id: Mapped[UUID] = _uuid_pk()
    study_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("studies.id", ondelete="CASCADE"), unique=True
    )
    aggregator_version: Mapped[str] = mapped_column(String(50))

    headline: Mapped[str] = mapped_column(Text)
    run_count: Mapped[int] = mapped_column(Integer)
    analysed_count: Mapped[int] = mapped_column(Integer)
    would_continue_rate: Mapped[float] = mapped_column(Float)
    purchase_intent: Mapped[dict] = mapped_column(JSONType, default=dict)
    retention_intent: Mapped[dict] = mapped_column(JSONType, default=dict)
    segments: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    study: Mapped[StudyRow] = relationship(back_populates="report")
    findings: Mapped[list[ConsensusFindingRow]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ConsensusFindingRow(Base):
    """A finding corroborated across runs. Counts are computed, never model output."""

    __tablename__ = "consensus_findings"

    id: Mapped[UUID] = _uuid_pk()
    report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("study_reports.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)

    statement: Mapped[str] = mapped_column(Text)
    dimension: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    claim_type: Mapped[str] = mapped_column(String(20))
    impact: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    agreement_count: Mapped[int] = mapped_column(Integer)
    run_count: Mapped[int] = mapped_column(Integer)
    segments: Mapped[list] = mapped_column(JSONType, default=list)
    severity_spread: Mapped[list] = mapped_column(JSONType, default=list)
    source_finding_ids: Mapped[list] = mapped_column(JSONType, default=list)
    run_ids: Mapped[list] = mapped_column(JSONType, default=list)
    quotes: Mapped[list] = mapped_column(JSONType, default=list)
    # Surfaced apart from the ranked findings so a blocked browser cannot
    # masquerade as research.
    likely_environmental: Mapped[bool] = mapped_column(Boolean, default=False)

    report: Mapped[StudyReportRow] = relationship(back_populates="findings")


class AppSettingsRow(Base):
    """Runtime-tunable configuration. Exactly one row.

    Only the settings that can genuinely take effect without a restart live
    here — model tiers, budgets, concurrency, browser geometry. Infrastructure
    (database, Redis, storage, keys) stays in the environment because it is
    baked into cached engines, the Dramatiq broker, and CORS middleware at
    import or boot; offering it as editable would mean edits that silently do
    nothing.

    Stored as one JSON blob rather than a row per key: the set of tunables
    changes with the code, and a schema migration per new knob buys nothing.
    """

    __tablename__ = "app_settings"

    id: Mapped[UUID] = _uuid_pk()
    values: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PersonaRow(Base):
    """A persona the operator authored or edited.

    Deliberately separate from `persona_cache`: that table is a cache, keyed by
    audience and resolver version, and is safe to evict or invalidate wholesale
    when the prompt changes. These rows are authored content and must never be
    discarded by either.

    The catalogue JSON stays the read-only set of built-ins. A row whose
    `persona_id` matches a built-in overrides it; a new id adds one. Editing a
    built-in therefore never writes to the source tree, and deleting the override
    restores the original.

    The whole validated model is stored as JSON rather than as fifteen columns,
    matching how a persona is already persisted onto runs and studies — the
    schema lives in `domain.persona.Persona`, and one definition is enough.
    """

    __tablename__ = "personas"

    id: Mapped[UUID] = _uuid_pk()
    persona_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    # "custom" — a new persona | "override" — replaces a catalogue built-in
    origin: Mapped[str] = mapped_column(String(20), default="custom")
    persona: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PersonaCacheRow(Base):
    """A resolved audience, kept so the same words always yield the same person.

    Reproducibility first — a study's segments must not drift between re-runs —
    and it keeps run creation off the model for every audience seen before,
    which matters because rate limit is this system's binding constraint.
    """

    __tablename__ = "persona_cache"
    __table_args__ = (
        UniqueConstraint("audience_key", "resolver_version", name="uq_persona_cache_key"),
    )

    id: Mapped[UUID] = _uuid_pk()
    audience_key: Mapped[str] = mapped_column(String(300), index=True)
    audience: Mapped[str] = mapped_column(String(300))
    # Bumping the resolver prompt invalidates every cached persona rather than
    # silently mixing two generations of output in one study.
    resolver_version: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(20))
    adopted_from: Mapped[str | None] = mapped_column(String(60), nullable=True)
    persona: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
