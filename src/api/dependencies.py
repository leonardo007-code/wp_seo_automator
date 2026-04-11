from __future__ import annotations

import logging

from fastapi import Depends, Request

from src.application.services.content_protection import ContentProtectionService
from src.application.services.diff_service import DiffService
from src.application.use_cases.modify_page import ModifyPageUseCase
from src.config.settings import LLMBackend, Settings, get_settings
from src.infrastructure.repositories.local_backup_repo import LocalBackupRepository
from src.infrastructure.wordpress.wp_rest_client import WpRestClient

logger = logging.getLogger(__name__)


# ── Lifespan-managed singletons ────────────────────────────────────────────────
# These are created in the lifespan (main.py) and stored in app.state.
# The dependency functions below retrieve them from app.state.


def get_wp_client(request: Request) -> WpRestClient:
    """Returns the shared WpRestClient singleton from app.state."""
    return request.app.state.wp_client


def get_backup_repo(request: Request) -> LocalBackupRepository:
    """Returns the shared LocalBackupRepository singleton from app.state."""
    return request.app.state.backup_repo


# ── Per-request dependencies ───────────────────────────────────────────────────
# These are lightweight and stateless — created fresh per request.
# Being DI functions means they can be overridden in tests trivially.


def get_protection_service() -> ContentProtectionService:
    return ContentProtectionService()


def get_diff_service() -> DiffService:
    return DiffService()


def get_llm_provider(settings: Settings = Depends(get_settings)):
    """
    Factory function: selects and instantiates the correct LLM provider
    based on the LLM_BACKEND environment variable.

    Adding a new provider:
        1. Create MyProvider in src/infrastructure/providers/
        2. Add a new LLMBackend enum value in config/settings.py
        3. Add an elif branch here.
        → Zero changes needed in routes, use cases, or domain.
    """
    if settings.llm_backend == LLMBackend.GEMINI:
        from src.infrastructure.providers.gemini_provider import GeminiProvider
        return GeminiProvider(settings)

    raise NotImplementedError(
        f"LLM backend {settings.llm_backend.value!r} is not yet implemented. "
        "Supported: 'gemini'. To add a new provider, see src/infrastructure/providers/."
    )


def get_modify_page_use_case(
    wp_client: WpRestClient = Depends(get_wp_client),
    llm_provider=Depends(get_llm_provider),
    protection_service: ContentProtectionService = Depends(get_protection_service),
    backup_repo: LocalBackupRepository = Depends(get_backup_repo),
    diff_service: DiffService = Depends(get_diff_service),
) -> ModifyPageUseCase:
    """
    Composes and returns the ModifyPageUseCase with all its dependencies.

    This is the PRIMARY dependency override point for tests:
        app.dependency_overrides[get_modify_page_use_case] = lambda: mock_use_case
    """
    return ModifyPageUseCase(
        wp_client=wp_client,
        llm_provider=llm_provider,
        protection_service=protection_service,
        backup_repo=backup_repo,
        diff_service=diff_service,
    )


# ── Lifespan factory helpers (called from main.py) ─────────────────────────────


def create_wp_client(settings: Settings) -> WpRestClient:
    """Creates the WpRestClient singleton for the lifespan."""
    logger.info(
        "Initializing WordPress client",
        extra={"wp_base_url": settings.wp_base_url},
    )
    return WpRestClient(settings)


def create_backup_repo(settings: Settings) -> LocalBackupRepository:
    """Creates the LocalBackupRepository singleton for the lifespan."""
    logger.info(
        "Initializing backup repository",
        extra={
            "backup_dir": str(settings.backup_dir),
            "log_dir": str(settings.log_dir),
        },
    )
    return LocalBackupRepository(settings)
