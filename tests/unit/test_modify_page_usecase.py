"""
Tests unitarios para ModifyPageUseCase.

Todos los colaboradores son mocks. Se testean:
- el flujo completo en dry_run y apply
- los guards (integridad, count mismatch)
- los early exits (sin segmentos)
- que el backup se llama siempre
- que update_page NO se llama en dry_run
- que errores del log no crashean la operación
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.application.use_cases.modify_page import ModifyPageUseCase
from src.domain.entities import (
    EditableSegment,
    ModificationStatus,
    PageContent,
    ProtectedContent,
    ValidationResult,
)
from src.domain.exceptions import ContentIntegrityError, LLMProviderError


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_page() -> PageContent:
    return PageContent(
        page_id=42,
        slug="servicios",
        title="Nuestros Servicios",
        raw_content="<h2>Servicios</h2><p>Ofrecemos consultoría especializada.</p>",
        url="https://site.com/servicios/",
        last_modified="2024-01-01",
        content_type="page",
    )


@pytest.fixture
def sample_segments() -> list[EditableSegment]:
    return [
        EditableSegment(index=0, tag="h2", text="Servicios"),
        EditableSegment(
            index=1,
            tag="p",
            text="Ofrecemos consultoría especializada.",
        ),
    ]


@pytest.fixture
def modified_segments(sample_segments) -> list[EditableSegment]:
    return [
        EditableSegment(
            index=0, tag="h2",
            text=sample_segments[0].text,
            modified_text="Servicios de consultoría profesional",
        ),
        EditableSegment(
            index=1, tag="p",
            text=sample_segments[1].text,
            modified_text="Brindamos asesoría empresarial de alto nivel.",
        ),
    ]


@pytest.fixture
def protected_content(sample_segments, sample_page) -> ProtectedContent:
    return ProtectedContent(
        raw_html=sample_page.raw_content,
        token_map={},
        segments=sample_segments,
        tokenized_html="<h2>⟦SEG_0⟧</h2><p>⟦SEG_1⟧</p>",
    )


def _build_use_case(
    page: PageContent,
    protected: ProtectedContent,
    modified_segs: list[EditableSegment],
    validation: ValidationResult | None = None,
    backup_path: str = "/backups/42/20240101.json",
) -> tuple[ModifyPageUseCase, dict]:
    """
    Factory helper: construye el use case con todos los mocks configurados.
    Devuelve el use case y un dict con los mocks para assertions.
    """
    wp = AsyncMock()
    wp.resolve_page_id.return_value = page.page_id
    wp.get_page_by_id.return_value = page
    wp.update_page.return_value = True

    llm = AsyncMock()
    llm.transform_segments.return_value = modified_segs

    protection = MagicMock()
    protection.extract_segments.return_value = protected
    protection.reconstruct.return_value = "<h2>Nuevo título</h2><p>Nuevo párrafo.</p>"
    protection.validate_integrity.return_value = validation or ValidationResult(
        is_valid=True, warnings=[], errors=[], missing_tokens=[]
    )

    backup = AsyncMock()
    backup.save_backup.return_value = backup_path
    backup.save_log.return_value = None

    diff = MagicMock()
    diff.generate_diff.return_value = "--- original\n+++ modified\n@@ -1 +1 @@\n"
    diff.generate_segments_diff.return_value = (
        "Changed segments: 2 of 2\n\n[H2 #0]\n  ORIGINAL: Servicios\n  MODIFIED: ..."
    )

    uc = ModifyPageUseCase(
        wp_client=wp,
        llm_provider=llm,
        protection_service=protection,
        backup_repo=backup,
        diff_service=diff,
    )

    mocks = {"wp": wp, "llm": llm, "protection": protection, "backup": backup, "diff": diff}
    return uc, mocks


# ── Tests: Happy Path ──────────────────────────────────────────────────────────


class TestHappyPath:

    async def test_dry_run_returns_result_without_publishing(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)

        result = await uc.execute("servicios", "mejora el SEO", dry_run=True)

        assert result.status == ModificationStatus.DRY_RUN
        assert result.dry_run is True
        mocks["wp"].update_page.assert_not_called()

    async def test_apply_mode_publishes_to_wordpress(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)

        result = await uc.execute("servicios", "mejora el SEO", dry_run=False)

        assert result.status == ModificationStatus.SUCCESS
        mocks["wp"].update_page.assert_called_once_with(
            page_id=sample_page.page_id,
            new_content=mocks["protection"].reconstruct.return_value,
            content_type=sample_page.content_type,
        )

    async def test_result_contains_correct_page_info(
        self, sample_page, protected_content, modified_segments
    ):
        uc, _ = _build_use_case(sample_page, protected_content, modified_segments)
        result = await uc.execute("42", "humaniza el contenido", dry_run=True)

        assert result.page_id == 42
        assert result.page_url == sample_page.url
        assert result.instruction == "humaniza el contenido"

    async def test_result_counts_changed_segments_correctly(
        self, sample_page, protected_content, modified_segments
    ):
        uc, _ = _build_use_case(sample_page, protected_content, modified_segments)
        result = await uc.execute("42", "reescribe", dry_run=True)

        assert result.segments_found == 2
        assert result.segments_modified == 2

    async def test_result_includes_diff_summary(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        result = await uc.execute("42", "optimiza", dry_run=True)

        assert result.diff_summary != ""
        mocks["diff"].generate_segments_diff.assert_called_once()

    async def test_backup_saved_before_any_change(
        self, sample_page, protected_content, modified_segments
    ):
        """El backup debe guardarse SIEMPRE, incluso en dry_run."""
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        result = await uc.execute("42", "optimiza", dry_run=True)

        mocks["backup"].save_backup.assert_called_once_with(
            page_id=42,
            original_content=sample_page.raw_content,
            metadata={
                "instruction": "optimiza",
                "url": sample_page.url,
                "slug": sample_page.slug,
                "dry_run": True,
            },
        )
        assert result.backup_path == "/backups/42/20240101.json"

    async def test_audit_log_saved_at_end(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        await uc.execute("42", "mejora", dry_run=True)

        mocks["backup"].save_log.assert_called()

    async def test_result_includes_original_and_proposed_content(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        result = await uc.execute("42", "reescribe", dry_run=True)

        assert result.original_content == sample_page.raw_content
        assert result.proposed_content == mocks["protection"].reconstruct.return_value

    async def test_url_identifier_resolved_correctly(
        self, sample_page, protected_content, modified_segments
    ):
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        await uc.execute("https://site.com/servicios/", "optimiza", dry_run=True)

        mocks["wp"].resolve_page_id.assert_called_once_with("https://site.com/servicios/")


# ── Tests: No Editable Segments ───────────────────────────────────────────────


class TestNoSegments:

    @pytest.fixture
    def empty_protected(self, sample_page) -> ProtectedContent:
        return ProtectedContent(
            raw_html=sample_page.raw_content,
            token_map={},
            segments=[],
            tokenized_html=sample_page.raw_content,
        )

    async def test_no_segments_returns_early_with_warning(
        self, sample_page, empty_protected
    ):
        uc, mocks = _build_use_case(sample_page, empty_protected, [])

        result = await uc.execute("42", "mejora", dry_run=True)

        assert result.segments_found == 0
        assert result.segments_modified == 0
        assert len(result.warnings) > 0
        mocks["llm"].transform_segments.assert_not_called()
        mocks["wp"].update_page.assert_not_called()

    async def test_no_segments_backup_still_saved(self, sample_page, empty_protected):
        """Incluso sin segmentos, el backup debe guardarse."""
        uc, mocks = _build_use_case(sample_page, empty_protected, [])
        await uc.execute("42", "mejora", dry_run=True)
        mocks["backup"].save_backup.assert_called_once()


# ── Tests: Guards de Seguridad ─────────────────────────────────────────────────


class TestGuards:

    async def test_integrity_failure_raises_content_integrity_error(
        self, sample_page, protected_content, modified_segments
    ):
        failed_validation = ValidationResult(
            is_valid=False,
            errors=["Missing protected token ⟦WP_0⟧"],
            warnings=[],
            missing_tokens=["<!-- wp:paragraph -->"],
        )
        uc, mocks = _build_use_case(
            sample_page, protected_content, modified_segments,
            validation=failed_validation,
        )

        with pytest.raises(ContentIntegrityError, match="integrity check failed"):
            await uc.execute("42", "optimiza", dry_run=False)

        # No debe publicar si la integridad falla
        mocks["wp"].update_page.assert_not_called()

    async def test_integrity_failure_saves_failure_log(
        self, sample_page, protected_content, modified_segments
    ):
        failed_validation = ValidationResult(
            is_valid=False,
            errors=["missing token"],
            warnings=[],
            missing_tokens=[],
        )
        uc, mocks = _build_use_case(
            sample_page, protected_content, modified_segments,
            validation=failed_validation,
        )

        with pytest.raises(ContentIntegrityError):
            await uc.execute("42", "optimiza", dry_run=False)

        # Debe haber guardado un log de fallo
        log_call_args = mocks["backup"].save_log.call_args[0][0]
        assert log_call_args["event"] == "integrity_failure"

    async def test_llm_count_mismatch_raises_llm_provider_error(
        self, sample_page, protected_content, sample_segments
    ):
        """
        Si el LLM devuelve un número diferente de segmentos al esperado,
        el use case debe detectarlo y lanzar LLMProviderError.
        """
        # Solo 1 segmento de vuelta pero había 2
        only_one = [
            EditableSegment(index=0, tag="h2", text="Servicios", modified_text="Nuevo título")
        ]
        uc, _ = _build_use_case(sample_page, protected_content, only_one)

        with pytest.raises(LLMProviderError, match="contract violation"):
            await uc.execute("42", "optimiza", dry_run=True)

    async def test_validation_warnings_included_in_result(
        self, sample_page, protected_content, modified_segments
    ):
        """Las advertencias de validación deben aparecer en el resultado aunque sea válido."""
        valid_with_warnings = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=["Reconstructed HTML is 250% of original"],
            missing_tokens=[],
        )
        uc, _ = _build_use_case(
            sample_page, protected_content, modified_segments,
            validation=valid_with_warnings,
        )

        result = await uc.execute("42", "amplía", dry_run=True)

        assert "250%" in result.warnings[0]


# ── Tests: Resilencia del Log ──────────────────────────────────────────────────


class TestLogResilience:

    async def test_log_error_does_not_propagate(
        self, sample_page, protected_content, modified_segments
    ):
        """
        Si guardar el log falla (disco lleno, permisos, etc.),
        el use case debe terminar correctamente igual.
        """
        uc, mocks = _build_use_case(sample_page, protected_content, modified_segments)
        mocks["backup"].save_log.side_effect = OSError("Disk full")

        # No debe lanzar excepción — el log no es crítico para el resultado
        result = await uc.execute("42", "optimiza", dry_run=True)
        assert result.status == ModificationStatus.DRY_RUN
