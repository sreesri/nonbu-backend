from functools import lru_cache
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./nonbu.db"
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60
    refresh_token_days: int = 60

    # OAuth client IDs accepted as the ID token audience (the Web client ID).
    google_client_ids: Annotated[list[str], NoDecode] = []
    # Only these Google accounts may sign in.
    allowed_emails: Annotated[list[str], NoDecode] = []

    _split = field_validator("google_client_ids", "allowed_emails", mode="before")(_split_csv)

    @field_validator("allowed_emails", mode="after")
    @classmethod
    def _lower(cls, value: list[str]) -> list[str]:
        return [email.lower() for email in value]

    @property
    def async_database_url(self) -> str:
        """Convert a plain Postgres URL (as given by Neon/Render) into an asyncpg URL."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if not url.startswith("postgresql://"):
            return url
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        # asyncpg understands `ssl`, not libpq's `sslmode` / `channel_binding`.
        if "sslmode" in query:
            query["ssl"] = query.pop("sslmode")
        query.pop("channel_binding", None)
        return urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=urlencode(query)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
