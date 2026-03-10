from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_list_separator=",",
    )

    # Telegram (리스너 전용 - API 서버에서는 불필요)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session_name: str = "report_collector"
    telegram_channels: list[str] = [
        "@repostory123",
        "@companyreport",
        "@searfin",
        "@cb_eq_research",
    ]

    # PostgreSQL (개별 설정 또는 DATABASE_URL 둘 다 지원)
    database_url_override: str | None = None  # Railway의 DATABASE_URL 환경변수
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
    llm_model: str = "claude-haiku-4-5-20251001"       # 메시지 분류/파싱용
    llm_pdf_model: str = "claude-sonnet-4-6"            # PDF 본문 분석용 (의도 파악)
    llm_max_retries: int = 2
    llm_timeout: int = 30

    # API 서버
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # 로깅
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            # Railway가 주는 postgresql://... → asyncpg 드라이버로 교체
            return self.database_url_override.replace(
                "postgres://", "postgresql+asyncpg://", 1
            ).replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        if self.database_url_override:
            return self.database_url_override.replace(
                "postgres://", "postgresql+psycopg2://", 1
            ).replace(
                "postgresql://", "postgresql+psycopg2://", 1
            )
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
