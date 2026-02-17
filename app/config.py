import os
from typing import List, Optional

from pydantic_settings import BaseSettings

ENV_FILE = "/etc/mist-userid/env"


class Settings(BaseSettings):
    pa_targets: str
    pa_api_key: str = ""  # Optional if pa_username/pa_password set
    pa_username: str = ""  # For API key auto-generation
    pa_password: str = ""  # For API key auto-generation
    redis_url: str = "redis://localhost:6379"
    mist_webhook_secret: str
    batch_size: int = 50
    batch_flush_interval: float = 2.0
    dedup_ttl: int = 300
    max_retry_attempts: int = 5
    userid_timeout: int = 60
    log_level: str = "INFO"
    log_format: str = "text"
    ignore_ssids: str = ""
    max_queue_depth: int = 10000
    webhook_max_age: int = 300  # seconds; reject events older than this

    def validate_pa_credentials(self) -> None:
        """Validate that either pa_api_key or pa_username+pa_password is set."""
        if not self.pa_api_key and not (self.pa_username and self.pa_password):
            raise ValueError(
                "Either PA_API_KEY or both PA_USERNAME and PA_PASSWORD must be set"
            )

    @property
    def pa_target_list(self) -> List[str]:
        return [t.strip() for t in self.pa_targets.split(",")]

    @property
    def ignore_ssid_set(self) -> set:
        if not self.ignore_ssids:
            return set()
        return {s.strip().lower() for s in self.ignore_ssids.split(",")}

    model_config = {
        "env_file": ENV_FILE if os.access(ENV_FILE, os.R_OK) else None,
        "env_file_encoding": "utf-8",
    }


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.validate_pa_credentials()
    return _settings
