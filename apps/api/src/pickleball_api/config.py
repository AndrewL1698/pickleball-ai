"""Typed settings for the API and the worker.

Everything the two processes must agree on lives here: the database, the Redis
queue, and where uploaded video is written. Values come from the environment or
a local `.env` file (see `.env.example`).

The two URLs are `SecretStr` because they carry credentials: a settings repr in
a log line, an exception, or a `/ready` response would otherwise print the
database password.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

MEGABYTE = 1024 * 1024

#: Extensions the API accepts, each mapped to the content type stored for it.
#: The stored type comes from this table, never from the client's multipart
#: header, which is attacker-controlled and wrong in practice (iOS commonly
#: sends `application/octet-stream` for a `.mov`).
VIDEO_CONTENT_TYPES: dict[str, str] = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
}


class Settings(BaseSettings):
    """Configuration shared by the FastAPI app and the RQ worker."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="PICKLEBALL_", extra="ignore"
    )

    environment: Environment = "development"

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://pickleball:pickleball@localhost:5433/pickleball"
    )
    database_echo: bool = False

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    queue_name: str = "analysis"
    job_timeout_seconds: int = Field(default=3600, gt=0)

    upload_dir: Path = Path("data/uploads")
    max_upload_bytes: int = Field(default=2048 * MEGABYTE, gt=0)
    allowed_video_extensions: tuple[str, ...] = tuple(VIDEO_CONTENT_TYPES)

    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")

    @field_validator("allowed_video_extensions", mode="after")
    @classmethod
    def _known_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(e.lower() if e.startswith(".") else f".{e.lower()}" for e in value)
        unknown = [e for e in normalized if e not in VIDEO_CONTENT_TYPES]
        if unknown:
            raise ValueError(
                f"no content type is defined for {unknown}; add it to VIDEO_CONTENT_TYPES"
            )
        return normalized

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read from the environment once."""
    return Settings()
