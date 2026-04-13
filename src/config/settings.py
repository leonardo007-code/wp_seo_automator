from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ruta absoluta a la raíz del proyecto.
# Resuelve: src/config/settings.py → src/config → src → raíz
# Garantiza que .env se encuentre sin importar el directorio de trabajo
# desde el que se lanza uvicorn o pytest.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class LLMBackend(str, Enum):
    """Proveedores LLM disponibles. Añadir aquí cuando se integre uno nuevo."""
    GEMINI = "gemini"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class AppEnv(str, Enum):
    """Entornos de ejecución disponibles."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """
    Configuración centralizada del sistema.

    Cargada desde:
      1. El archivo .env en la raíz del proyecto (ruta absoluta — funciona
         independientemente del directorio de trabajo).
      2. Variables de entorno del sistema (tienen precedencia sobre .env).

    Ningún otro módulo debe leer os.environ directamente.
    Para tests: get_settings.cache_clear() + os.environ.setdefault(...)
    """

    model_config = SettingsConfigDict(
        # Ruta absoluta: funciona al lanzar desde cualquier directorio.
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Entorno de ejecución ────────────────────────────────────────────────────
    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug_mode: bool = False

    # ── WordPress ───────────────────────────────────────────────────────────────
    wp_base_url: str
    wp_api_user: str
    wp_api_app_password: str

    # ── LLM ────────────────────────────────────────────────────────────────────
    llm_backend: LLMBackend = LLMBackend.GEMINI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # ── Comportamiento ──────────────────────────────────────────────────────────
    # SEGURIDAD: siempre True por defecto — protege contra publicar accidentalmente.
    # Para publicar de verdad, pasar dry_run=False explícitamente en el request body.
    dry_run_default: bool = True

    # ── Storage ─────────────────────────────────────────────────────────────────
    backup_dir: Path = _PROJECT_ROOT / "backups"
    log_dir: Path = _PROJECT_ROOT / "logs"
    log_level: str = "INFO"

    # ── HTTP / Resilience ────────────────────────────────────────────────────────
    request_timeout_seconds: int = 30
    max_retries: int = 3

    # ── Propiedades derivadas ────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app_env == AppEnv.DEVELOPMENT

    # ── Validadores ─────────────────────────────────────────────────────────────

    @field_validator("wp_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}, got: {v!r}")
        return upper

    @field_validator("app_host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def ensure_storage_dirs_exist(self) -> Settings:
        """Crea los directorios de backup y log al arrancar si no existen."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self

    @model_validator(mode="after")
    def warn_if_gemini_key_missing(self) -> Settings:
        """
        Detecta temprano si falta la API key de Gemini cuando el backend es Gemini.
        No lanza una excepción aquí para no bloquear el arranque cuando se usa
        un backend diferente — GeminiProvider valida esto en su __init__.
        """
        if self.llm_backend == LLMBackend.GEMINI and not self.gemini_api_key:
            import warnings
            warnings.warn(
                "LLM_BACKEND=gemini pero GEMINI_API_KEY está vacía. "
                "El servidor arrancará pero fallará al procesar requests. "
                "Configura GEMINI_API_KEY en tu .env.",
                stacklevel=2,
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Factory function — singleton via lru_cache.

    Usar como dependencia en FastAPI: Depends(get_settings).
    La instancia se crea una sola vez y se reutiliza en todos los requests.

    En tests: limpiar con get_settings.cache_clear() antes de instanciar
    con variables de entorno distintas.
    """
    return Settings()
