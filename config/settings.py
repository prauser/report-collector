from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_list_separator=",",
    )

    # Telegram
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_name: str = "report_collector"
    telegram_channels: list[str] = [
        "@repostory123",
        "@companyreport",
        "@searfin",
        "@cb_eq_research",
    ]

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "report_collector"
    postgres_user: str = "rcuser"
    postgres_password: str = "rcpassword"

    # PDF
    pdf_base_path: Path = Path("./data/pdfs")

    # 백필
    backfill_limit: int = 1000  # 0 = 전체

    # 로깅
    log_level: str = "INFO"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
