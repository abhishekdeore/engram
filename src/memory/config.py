from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


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

    # ── Embeddings (Phase 2) ─────────────────────────────────────────────────
    openai_api_key: str = Field(default="")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimensions: int = Field(default=1536)

    # ── Redis Cache (Phase 2) ────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379")
    embedding_cache_ttl_seconds: int = Field(default=3600)

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()
