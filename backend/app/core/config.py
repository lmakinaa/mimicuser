"""Settings. Validated at boot — a missing ENCRYPTION_KEY fails the process
rather than silently signing OAuth state with an empty secret.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    DATABASE_URL: str = "postgresql+asyncpg://mimic:mimic@localhost:5433/mimicuser"
    REDIS_URL: str = "redis://localhost:6380/0"
    STORAGE_ROOT: Path = Path("./storage")
    WORKDIR: Path = Path("./.runs")

    ENCRYPTION_KEY: str = ""

    DEFAULT_MODEL: str = "claude-opus-5"
    # Tiered on purpose: the customer agent is long, tool-heavy and run many
    # times per study, so it uses the cheaper tier; judgement work stays on the
    # stronger model. Rate limit, not money, is the binding constraint here —
    # subscription auth reports no dollar cost at all.
    CUSTOMER_MODEL: str = "claude-sonnet-5"
    AGGREGATOR_MODEL: str = "claude-opus-5"
    # Audience resolution is one small structured call on the critical path of
    # every new audience. Running it on the flagship put run creation behind
    # the same rate limit as the evaluator, which is how a form submit ends up
    # failing with a 429. Haiku is ample for adopt-or-write.
    RESOLVER_MODEL: str = "claude-haiku-4-5"
    STUDY_CONCURRENCY: int = 3
    DEFAULT_ENGINE: str = "claude_agent"
    # Evaluation runs once per customer, so on a study of nine it is nine Opus
    # calls landing together — which is exactly how every evaluation so far has
    # been rate-limited into an empty report. A Sonnet report that exists beats
    # an Opus one that 429s. Aggregation stays on Opus: it runs once per study,
    # and clustering across customers is the harder judgement.
    EVALUATOR_MODEL: str = "claude-sonnet-5"
    MODEL_EFFORT: str = "high"

    CLAUDE_OAUTH_CLIENT_ID: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    OAUTH_CALLBACK_URL: str = "https://platform.claude.com/oauth/code/callback"
    OAUTH_STATE_TTL_SECONDS: int = 600
    ANTHROPIC_API_KEY: str = ""
    LLM_PROVIDER: str = "anthropic"
    OPENAI_DEFAULT_MODEL: str = "gpt-4o-mini"
    OPENAI_CUSTOMER_MODEL: str = "gpt-4o-mini"
    OPENAI_EVALUATOR_MODEL: str = "gpt-4o"
    OPENAI_AGGREGATOR_MODEL: str = "gpt-4o"
    OPENAI_RESOLVER_MODEL: str = "gpt-4o-mini"

    BROWSER_HEADLESS: bool = True
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 900

    DEFAULT_MAX_TOOL_CALLS: int = 60
    DEFAULT_MAX_WALL_SECONDS: int = 600
    DEFAULT_MAX_COST_USD: float = 3.0

    CORS_ORIGINS: str = "http://localhost:3000"

    # A public demo: operator surfaces (credentials, settings, destructive
    # endpoints) are refused, and new work runs only on the visitor's own key.
    PUBLIC_MODE: bool = False

    LOG_LEVEL: str = "INFO"

    ANTHROPIC_MODELS: ClassVar[tuple[str, ...]] = (
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
    )
    OPENAI_MODELS: ClassVar[tuple[str, ...]] = (
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "o3",
        "o4-mini",
    )

    @field_validator("STORAGE_ROOT", "WORKDIR")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.resolve()

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def credential_kind(self) -> str:
        """API key wins only when it is the *only* thing configured.

        OAuth is the documented path for this product (subscription auth), so a
        stray key in the environment must not silently redirect billing.
        """
        return "api_key" if self.ANTHROPIC_API_KEY else "oauth"

    @property
    def engine_name(self) -> str:
        """The registered agent adapter for the currently selected provider."""
        return "openai" if self.LLM_PROVIDER == "openai" else "claude_agent"

    def model_for_provider(self, provider: str, tier: str) -> str:
        """Return a tier model that is valid for a specific provider."""
        configured = getattr(self, f"{tier.upper()}_MODEL")
        models = self.OPENAI_MODELS if provider == "openai" else self.ANTHROPIC_MODELS
        if configured in models:
            return configured
        fallback = getattr(self, f"OPENAI_{tier.upper()}_MODEL") if provider == "openai" else configured
        return fallback if fallback in models else models[0]

    def model_for(self, tier: str) -> str:
        """Return a tier model that is valid for the selected provider."""
        return self.model_for_provider(self.LLM_PROVIDER, tier)

    def valid_models(self, provider: str) -> tuple[str, ...]:
        return self.OPENAI_MODELS if provider == "openai" else self.ANTHROPIC_MODELS

    def require_encryption_key(self) -> str:
        if not self.ENCRYPTION_KEY:
            raise RuntimeError(
                "ENCRYPTION_KEY is not set. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        return self.ENCRYPTION_KEY


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
