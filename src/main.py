from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.dependencies import create_backup_repo, create_wp_client
from src.api.routes.modifications import router as modifications_router
from src.config.settings import get_settings
from src.domain.exceptions import (
    ContentIntegrityError,
    LLMProviderError,
    WordPressAPIError,
    WordPressAuthError,
    WordPressPageNotFoundError,
)

settings = get_settings()


# ── Logging ────────────────────────────────────────────────────────────────────


def configure_logging() -> None:
    """
    Configura structlog para JSON en producción y texto legible en DEBUG.
    Centralizado aquí — ningún otro módulo configura su propio logger.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    renderer = (
        structlog.dev.ConsoleRenderer()
        if settings.log_level.upper() == "DEBUG"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)


# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the lifecycle of shared singletons.

    On startup:
      - WpRestClient: creates the httpx connection pool (expensive, share it).
      - LocalBackupRepository: ensures backup/log directories exist.

    On shutdown:
      - Closes the httpx client properly (releases TCP connections).

    Per-request dependencies (LLM provider, use case, etc.) are NOT created here.
    They live in src/api/dependencies.py and can be overridden in tests.
    """
    logger = logging.getLogger(__name__)
    logger.info("Starting WP SEO Automator...")

    app.state.wp_client = create_wp_client(settings)
    app.state.backup_repo = create_backup_repo(settings)

    logger.info(
        "Application ready",
        extra={
            "llm_backend": settings.llm_backend.value,
            "dry_run_default": settings.dry_run_default,
            "wp_base_url": settings.wp_base_url,
        },
    )
    yield

    logger.info("Shutting down — closing HTTP client...")
    await app.state.wp_client.close()


# ── Exception Handlers ─────────────────────────────────────────────────────────
# Domain exceptions are mapped to HTTP status codes here.
# The route layer stays clean — it never catches exceptions.


def _error_response(status_code: int, detail: str, error_type: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "error_type": error_type},
    )


# ── App Factory ────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title="WP SEO Automator",
        description=(
            "Sistema de automatización de contenido en WordPress con IA desacoplada. "
            "Soporta Gemini, Ollama, y cualquier endpoint OpenAI-compatible."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Exception handlers ─────────────────────────────────────────────────────

    @app.exception_handler(WordPressPageNotFoundError)
    async def handle_wp_not_found(_: Request, exc: WordPressPageNotFoundError):
        return _error_response(404, str(exc), "page_not_found")

    @app.exception_handler(WordPressAuthError)
    async def handle_wp_auth(_: Request, exc: WordPressAuthError):
        return _error_response(401, str(exc), "authentication_error")

    @app.exception_handler(WordPressAPIError)
    async def handle_wp_api(_: Request, exc: WordPressAPIError):
        return _error_response(502, str(exc), "wordpress_api_error")

    @app.exception_handler(ContentIntegrityError)
    async def handle_integrity(_: Request, exc: ContentIntegrityError):
        return _error_response(422, str(exc), "integrity_validation_failed")

    @app.exception_handler(LLMProviderError)
    async def handle_llm(_: Request, exc: LLMProviderError):
        return _error_response(503, str(exc), "llm_provider_error")

    @app.exception_handler(NotImplementedError)
    async def handle_not_impl(_: Request, exc: NotImplementedError):
        return _error_response(501, str(exc), "not_implemented")

    # ── Routes ─────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["System"])
    async def health_check() -> dict:
        """Verifica que el servidor esté activo y muestra la configuración activa."""
        return {
            "status": "ok",
            "llm_backend": settings.llm_backend.value,
            "dry_run_default": settings.dry_run_default,
            "wp_base_url": settings.wp_base_url,
        }

    app.include_router(modifications_router, prefix="/api/v1")

    return app


app = create_app()
