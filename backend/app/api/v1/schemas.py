"""HTTP request/response shapes. Boundary only — never used internally."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from domain.findings import RunReport, StudyReport
from domain.persona import Persona

# Pydantic's HttpUrl only rejects URLs longer than 2083 characters, but every
# product_url column is String(2048). A URL in that gap validated here and then
# failed at INSERT with a 500, so the boundary enforces the column's limit.
_URL_LIMIT = 2048

# -- projects ---------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    product_url: HttpUrl = Field(max_length=_URL_LIMIT)
    product_description: str | None = None
    default_audience: str | None = None


class UpdateProjectRequest(BaseModel):
    """Partial edit — unset fields are left alone."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    product_url: HttpUrl | None = Field(default=None, max_length=_URL_LIMIT)
    product_description: str | None = None
    default_audience: str | None = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    product_url: str
    product_description: str | None
    default_audience: str | None
    created_at: datetime
    run_count: int = 0
    study_count: int = 0


# -- runs -------------------------------------------------------------------


class BudgetRequest(BaseModel):
    max_tool_calls: int = Field(default=60, ge=5, le=300)
    max_wall_seconds: int = Field(default=600, ge=60, le=3600)
    max_cost_usd: float = Field(default=3.0, gt=0, le=50)


class CreateRunRequest(BaseModel):
    """The only two required fields are the two the brief requires."""

    product_url: HttpUrl = Field(max_length=_URL_LIMIT)
    audience: str = Field(min_length=2, max_length=300)

    # Everything below is optional customisation.
    task: str | None = Field(default=None, max_length=2000)
    project_id: UUID | None = None
    persona_id: str | None = None
    persona_overrides: dict[str, Any] | None = None
    budget: BudgetRequest | None = None
    model: str | None = None
    segment_key: str | None = None

    @field_validator("task")
    @classmethod
    def _blank_task_is_no_task(cls, value: str | None) -> str | None:
        # An empty string must select autonomous mode, not a directed run with
        # an empty objective.
        return value.strip() if value and value.strip() else None


class RunSummaryResponse(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    mode: str
    product_url: str
    audience: str
    task: str | None
    persona_label: str
    persona_id: str
    persona_source: str
    match_confidence: float
    engine: str
    model: str
    outcome: str | None = None
    would_continue: bool | None = None
    stop_reason: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class RunDetailResponse(RunSummaryResponse):
    customer: dict[str, Any]
    budget: dict[str, Any]
    prompt_versions: dict[str, Any]
    event_count: int
    report: RunReport | None = None


class EventResponse(BaseModel):
    seq: int
    ts: datetime
    type: str
    url: str | None
    action: str | None
    target: str | None
    input: dict[str, Any] | None
    result: dict[str, Any] | None
    observation: str | None
    screenshot_url: str | None
    error: str | None
    meta: dict[str, Any]


class EventPage(BaseModel):
    run_id: UUID
    events: list[EventResponse]
    next_after_seq: int | None


# -- personas ---------------------------------------------------------------


class PersonaEntryResponse(BaseModel):
    """A persona plus where it came from, so the UI knows what it may offer."""

    persona: Persona
    # "builtin" (catalogue, untouched) | "override" (edited built-in) | "custom"
    origin: str
    editable: bool
    deletable: bool


class PersonaListResponse(BaseModel):
    version: int
    # Kept for existing clients that only want the personas themselves.
    personas: list[Persona]
    entries: list[PersonaEntryResponse] = Field(default_factory=list)


class PersonaTraitsRequest(BaseModel):
    """All optional so PATCH can change one trait without restating the rest."""

    technical_ability: str | None = None
    domain_knowledge: str | None = None
    patience: str | None = None
    price_sensitivity: str | None = None
    risk_tolerance: str | None = None
    exploration_style: str | None = None


class PersonaWriteRequest(BaseModel):
    """Create or edit a persona.

    Everything is optional because PATCH shares this shape; POST validates the
    result against `domain.persona.Persona`, which is where the real
    requirements live. Trait values are checked against the six StrEnums there,
    so an invalid level is a 422 rather than a persona that breaks at prompt
    time.
    """

    id: str | None = Field(default=None, max_length=60)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    summary: str | None = None
    background: str | None = None
    motivation: str | None = None
    aliases: list[str] | None = None
    goals: list[str] | None = None
    typical_objections: list[str] | None = None
    preferences: list[str] | None = None
    vocabulary: list[str] | None = None
    context_notes: list[str] | None = None
    expected_behavior: list[str] | None = None
    traits: PersonaTraitsRequest | None = None


class ResolveAudienceRequest(BaseModel):
    audience: str = Field(min_length=2, max_length=300)


class ResolveAudienceResponse(BaseModel):
    persona: Persona
    source: str
    method: str
    matched_id: str | None
    match_confidence: float
    notes: str | None


# -- oauth ------------------------------------------------------------------


class StartOAuthRequest(BaseModel):
    label: str = Field(default="Anthropic account", max_length=200)


class StartOAuthResponse(BaseModel):
    auth_url: str
    state: str
    expires_in: int
    instructions: str


class SubmitCodeRequest(BaseModel):
    code: str = Field(min_length=4)
    state: str = Field(min_length=8)


class OAuthAccountResponse(BaseModel):
    id: UUID
    label: str
    email: str | None
    provider: str
    token_expires_at: datetime | None
    created_at: datetime


class AuthStatusResponse(BaseModel):
    configured: bool
    credential_kind: str
    accounts: list[OAuthAccountResponse]


class ApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=10, max_length=500)


class ProviderStatusResponse(BaseModel):
    active_provider: str
    anthropic_configured: bool
    openai_configured: bool


# -- studies ----------------------------------------------------------------


class SegmentRequest(BaseModel):
    audience: str = Field(min_length=2, max_length=300)
    count: int = Field(default=3, ge=1, le=15)
    persona_id: str | None = None


class CreateStudyRequest(BaseModel):
    """One product, several audience segments, several customers each."""

    product_url: HttpUrl = Field(max_length=_URL_LIMIT)
    segments: list[SegmentRequest] = Field(min_length=1, max_length=6)
    name: str | None = Field(default=None, max_length=200)
    task: str | None = Field(default=None, max_length=2000)
    project_id: UUID | None = None
    budget: BudgetRequest | None = None

    @field_validator("task")
    @classmethod
    def _blank_task_is_no_task(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    @field_validator("segments")
    @classmethod
    def _total_runs_is_sane(cls, value: list[SegmentRequest]) -> list[SegmentRequest]:
        # Rate limit, not money, is the binding constraint; a runaway study
        # starves every other run on the same subscription.
        total = sum(s.count for s in value)
        if total > 30:
            raise ValueError(f"{total} runs is too many for one study (max 30)")
        return value


class StudySummaryResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    status: str
    product_url: str
    task: str | None
    segments: list[dict[str, Any]]
    progress: dict[str, int]
    headline: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class StudyDetailResponse(StudySummaryResponse):
    models: dict[str, Any]
    budget: dict[str, Any]
    prompt_versions: dict[str, Any]
    error: str | None = None
    report: StudyReport | None = None
    runs: list[dict[str, Any]] = Field(default_factory=list)


# -- settings ---------------------------------------------------------------


class SettingsResponse(BaseModel):
    """Effective values, plus what is and is not changeable here."""

    editable: dict[str, Any]
    descriptions: dict[str, str]
    #: Keys currently pinned in the database rather than taken from the env.
    overridden: list[str]
    read_only: dict[str, str]
    restart_required: list[str]


class UpdateSettingsRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
