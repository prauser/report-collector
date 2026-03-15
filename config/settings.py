from pathlib import Path
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_list_separator=",",
        env_ignore_empty=True,
    )

    # Telegram (리스너 전용 - API 서버에서는 불필요)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session_name: str = "report_collector"
    telegram_session_string: str | None = None  # Railway용 StringSession
    telegram_channels: Annotated[list[str], NoDecode] = [
        "@repostory123",
        "@companyreport",
        "@searfin",
        "@cb_eq_research",
    ]

    @field_validator("telegram_channels", mode="before")
    @classmethod
    def _parse_channels(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    # PostgreSQL - DATABASE_URL (Railway 표준) 또는 개별 설정
    database_url: str | None = None  # Railway가 자동으로 DATABASE_URL 주입
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "report_collector"
    postgres_user: str = "rcuser"
    postgres_password: str = "rcpassword"

    # PDF
    pdf_base_path: Path = Path("./data/pdfs")

    # 백필
    backfill_limit: int = 1000  # 0 = 전체

    # LLM (Anthropic)
    anthropic_api_key: str | None = None
    llm_enabled: bool = True
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_pdf_model: str = "claude-sonnet-4-6"
    llm_max_retries: int = 2
    llm_timeout: int = 30

    # Layer 2 분석
    analysis_enabled: bool = True
    markdown_converter: str = "pymupdf4llm"   # or "marker"
    analysis_schema_version: str = "v1"
    analysis_batch_size: int = 10

    # API 서버
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # 로깅
    log_level: str = "INFO"

    def _convert_url(self, driver: str) -> str:
        """postgresql:// 또는 postgres:// → 지정 드라이버로 변환."""
        url = self.database_url or ""
        return (
            url
            .replace("postgres://", f"postgresql+{driver}://", 1)
            .replace("postgresql://", f"postgresql+{driver}://", 1)
        )

    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return self._convert_url("asyncpg")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        if self.database_url:
            return self._convert_url("psycopg2")
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
