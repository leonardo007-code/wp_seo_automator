from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMBackend(str, Enum):
    """Proveedores LLM disponibles. Añadir aquí cuando se integre uno nuevo."""
    GEMINI = "gemini"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class Settings(BaseSettings):
    """
    Configuración centralizada del sistema.
    Cargada desde variables de entorno o archivo .env.
    Ningún otro módulo debe leer os.environ directamente.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- WordPress ---
    wp_base_url: str
    wp_api_user: str
    wp_api_app_password: str

    # --- LLM ---
    llm_backend: LLMBackend = LLMBackend.GEMINI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # --- Behavior ---
    dry_run_default: bool = True

    # --- Storage ---
    backup_dir: Path = Path("./backups")
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"

    # --- HTTP / Resilience ---
    request_timeout_seconds: int = 30
    max_retries: int = 3

    @field_validator("wp_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def ensure_storage_dirs_exist(self) -> Settings:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Factory function — singleton via lru_cache.
    Usar como dependencia en FastAPI: Depends(get_settings).
    La instancia se crea una sola vez y se reutiliza en todos los requests.
    En tests, limpiar con get_settings.cache_clear() si se modifican env vars.
    """
    return Settings()
