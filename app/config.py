import os
from typing import List, Optional

from pydantic_settings import BaseSettings

ENV_FILE = "/etc/mist-userid/env"


class Settings(BaseSettings):
    pa_targets: str
    pa_api_key: str
    redis_url: str = "redis://localhost:6379"
    mist_webhook_secret: str
    batch_size: int = 50
    batch_flush_interval: float = 2.0
    dedup_ttl: int = 300
    max_retry_attempts: int = 5
    userid_timeout: int = 60
    log_level: str = "INFO"

    @property
    def pa_target_list(self) -> List[str]:
        return [t.strip() for t in self.pa_targets.split(",")]

    model_config = {
        "env_file": ENV_FILE if os.access(ENV_FILE, os.R_OK) else None,
        "env_file_encoding": "utf-8",
    }


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
