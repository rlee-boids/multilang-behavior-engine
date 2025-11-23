from enum import Enum

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIProvider(str, Enum):
    google = "google"
    openai = "openai"


class Settings(BaseSettings):
    # Pydantic v2-style settings config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # <--- ignore unknown keys from .env instead of raising
    )

    # ------------ Core app ------------
    PROJECT_NAME: str = Field(
        default="MultiLang Behavior Engine",
        env="PROJECT_NAME",
    )
    API_V1_STR: str = Field(
        default="/api/v1",
        env="API_V1_STR",
    )

    # ------------ Database ------------
    # REQUIRED: must come from .env or environment
    DATABASE_URL: str = Field(
        ...,
        env="DATABASE_URL",
        description="SQLAlchemy database URL for PostgreSQL",
    )

    # ------------ Container / Podman ------------
    CONTAINER_RUNTIME: str = Field(
        default="podman",
        env="CONTAINER_RUNTIME",
        description="Container runtime binary (podman by default).",
    )
    CONTAINER_NETWORK: str = Field(
        default="bridge",
        env="CONTAINER_NETWORK",
        description="Default network name used when running containers.",
    )

    # ------------ Analyzer / repo scanning ------------
    # Support both UPPERCASE and lowercase env names so your existing .env works
    ANALYZER_WORKSPACE_ROOT: str = Field(
        default="./workspace",
        env=["ANALYZER_WORKSPACE_ROOT", "analyzer_workspace_root"],
    )
    MAX_ANALYZER_FILE_BYTES: int = Field(
        default=50_000,
        env=["MAX_ANALYZER_FILE_BYTES", "max_analyzer_file_bytes"],
    )

    # ------------ AI provider selection ------------
    AI_PROVIDER: AIProvider = Field(
        default=AIProvider.google,
        env="AI_PROVIDER",
        description="Which AI backend to use for analysis (google|openai).",
    )

    # ------------ Google AI (Gemini) ------------
    GOOGLE_API_KEY: str | None = Field(default=None, env="GOOGLE_API_KEY")
    # Default to the working model you just switched to
    GOOGLE_MODEL_NAME: str = Field(
        default="gemini-2.5-flash",
        env="GOOGLE_MODEL_NAME",
    )

    # ------------ OpenAI (GPT) ------------
    OPENAI_API_KEY: str | None = Field(default=None, env="OPENAI_API_KEY")
    # Default to a generally-available GPT model; override via env if needed
    OPENAI_MODEL_NAME: str = Field(
        default="gpt-4.1-mini",
        env="OPENAI_MODEL_NAME",
    )

    # ------------ GitHub integration ------------
    GITHUB_TOKEN: str | None = Field(default=None, env="GITHUB_TOKEN")
    GITHUB_OWNER_TYPE: str = Field(
        default="user",
        env="GITHUB_OWNER_TYPE",
        description="GitHub owner type: 'user' or 'org'.",
    )
    GITHUB_OWNER_NAME: str | None = Field(
        default=None,
        env="GITHUB_OWNER_NAME",
        description="GitHub username or org name used as owner.",
    )
    GITHUB_API_BASE_URL: str = Field(
        default="https://api.github.com",
        env="GITHUB_API_BASE_URL",
    )
    GITHUB_REPO_PREFIX: str = Field(
        default="multilang-converted-",
        env="GITHUB_REPO_PREFIX",
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def _require_db_url(cls, v: str) -> str:
        if not v:
            raise ValueError("DATABASE_URL must be set in environment or .env")
        return v


settings = Settings()
