from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing_extensions import Self

_INSECURE_JWT_DEFAULTS = {
    "change_this_in_production",
    "secret",
    "password",
    "jwt_secret",
    "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Neo4j ────────────────────────────────────────────────────────────────
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_username: str = Field(default="neo4j")
    neo4j_password: str = Field(...)          # required — no default
    neo4j_database: str = Field(default="neo4j")

    # Connection pool
    neo4j_max_connection_pool_size: int = Field(default=10)
    neo4j_connection_timeout_seconds: int = Field(default=10)

    # ── Application ──────────────────────────────────────────────────────────
    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)

    # ── Auth (Phase 1) ───────────────────────────────────────────────────────
    jwt_secret_key: str = Field(default="change_this_in_production")
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=10080)  # 7 days

    # ── Rate Limiting (Phase 3, tightened Phase 6) ──────────────────────────────
    # Applied per authenticated userId.
    # Phase 6: production-appropriate defaults (30 writes/min, 20 queries/min).
    # Override via .env for development: RATE_LIMIT_WRITE_PER_MINUTE=600
    rate_limit_write_per_minute: int = Field(default=30)
    rate_limit_query_per_minute: int = Field(default=20)

    # ── Embeddings (Phase 2) ─────────────────────────────────────────────────
    openai_api_key: str = Field(default="")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536)

    # ── Redis Cache (Phase 2) ────────────────────────────────────────────────
    redis_url: str = Field(default="")
    embedding_cache_ttl_seconds: int = Field(default=3600)

    # ── CORS (Phase 6) ──────────────────────────────────────────────────────────
    # Comma-separated origins for production, e.g.:
    #   CORS_ALLOWED_ORIGINS=https://engram-production-d6d1.up.railway.app,https://chatgpt.com
    cors_allowed_origins: str = Field(default="")

    # ── Usage Limits (Phase 6) ───────────────────────────────────────────────
    free_tier_message_limit: int = Field(default=10000)
    free_tier_daily_query_limit: int = Field(default=100)

    # ── MCP HTTP server (Phase 5) ─────────────────────────────────────────────
    mcp_http_port: int = Field(default=8001)
    mcp_http_host: str = Field(default="0.0.0.0")

    @model_validator(mode="after")
    def _validate_production_safety(self) -> Self:
        """
        P3-PRE-9: Refuse to start in production with an insecure JWT secret.
        Fail-fast is the only safe default — a misconfigured secret silently
        makes every token forgeable.
        """
        if self.app_env == "production":
            if self.jwt_secret_key.lower() in _INSECURE_JWT_DEFAULTS:
                raise ValueError(
                    "JWT_SECRET_KEY must be set to a strong random value in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must be at least 32 characters in production."
                )
        return self

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
