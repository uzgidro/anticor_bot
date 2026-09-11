"""Application configuration via pydantic-settings.

Secrets are wrapped in SecretStr so they never appear in logs or reprs.
Nested settings (Postgres, Redis) use the ``__`` env delimiter, e.g.
``POSTGRES__HOST``.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOCALES: tuple[str, ...] = ("ru", "kaa", "uz_cyrl", "uz_latn", "en")


class PostgresSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    user: str = "anticor"
    password: SecretStr = SecretStr("")
    db: str = "anticor"

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.db}"
        )


class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    password: SecretStr = SecretStr("")
    db: int = 0

    @property
    def dsn(self) -> str:
        pwd = self.password.get_secret_value()
        auth = f":{pwd}@" if pwd else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class MatrixSettings(BaseSettings):
    """Matrix (Element) bridge. Empty homeserver/user = bridge disabled.

    Rooms are per submission type; membership of a room is what provisions a
    User with the matching resp_<type> flag (see services/actions + matrix/bridge).
    """

    homeserver: str = ""
    user: str = ""
    password: SecretStr = SecretStr("")
    token: SecretStr = SecretStr("")
    room_appeal: str = ""
    room_corruption: str = ""
    locale: str = "uz_latn"
    store_dir: str = "matrix_store"
    device_name: str = "anticor-bot"

    @property
    def enabled(self) -> bool:
        has_secret = bool(self.password.get_secret_value() or self.token.get_secret_value())
        return bool(self.homeserver and self.user and has_secret)

    def room_for(self, type_value: str) -> str:
        """Room id for a SubmissionType value ('appeal' | 'corruption'), '' if unset."""
        return self.room_appeal if type_value == "appeal" else self.room_corruption

    def type_for_room(self, room_id: str) -> str | None:
        """Inverse of room_for: which submission type a room serves, or None."""
        if room_id and room_id == self.room_appeal:
            return "appeal"
        if room_id and room_id == self.room_corruption:
            return "corruption"
        return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Telegram
    bot_token: SecretStr
    use_webhook: bool = False
    webhook_url: str = "https://example.com"
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr = SecretStr("")
    webhook_host: str = "127.0.0.1"  # bind localhost; TLS terminates at the proxy
    webhook_port: int = 8080
    drop_pending_updates: bool = True

    admin_ids: Annotated[set[int], NoDecode] = Field(default_factory=set)

    # Anonymity
    anon_enc_key: SecretStr

    # Nested
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    matrix: MatrixSettings = Field(default_factory=MatrixSettings)

    # App
    debug: bool = False
    default_locale: str = "ru"
    locales: tuple[str, ...] = LOCALES
    # Apply Alembic migrations (upgrade head) on boot. Convenient for dev and a
    # single replica. In prod with multiple replicas set this to false and run
    # migrations as a separate one-shot step so replicas don't race the upgrade.
    run_migrations_on_startup: bool = True

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return {int(x.strip()) for x in v.split(",") if x.strip()}
        return v

    @model_validator(mode="after")
    def _validate_webhook(self) -> Settings:
        if self.use_webhook:
            if not self.webhook_url or self.webhook_url == "https://example.com":
                raise ValueError("WEBHOOK_URL must be set when USE_WEBHOOK=true")
            if not self.webhook_url.startswith("https://"):
                raise ValueError("WEBHOOK_URL must be HTTPS")
            if not self.webhook_secret.get_secret_value():
                raise ValueError("WEBHOOK_SECRET must be set when USE_WEBHOOK=true")
        return self

    @property
    def webhook_full_url(self) -> str:
        return self.webhook_url.rstrip("/") + self.webhook_path
