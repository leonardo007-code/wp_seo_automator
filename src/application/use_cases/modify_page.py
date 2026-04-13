"""
modify_page.py — Caso de uso principal: modificación de contenido WordPress.

Orquesta el pipeline completo:
  1.  Resolve identifier → page_id
  2.  Fetch page by ID → PageContent (raw HTML / shortcodes)
  3.  Detect builder → ExtractionReport
  4.  Select extraction strategy based on builder
  5.  Extract editable segments
  6.  [Early exit] if no segments found
  7.  [Policy check] if publish is blocked and dry_run=False → raise error
  8.  Transform segments via LLM
  9.  [Guard] Validate segment count matches
  10. Reconstruct content with modified text + restored tokens
  11. Validate structural integrity → ValidationResult
  12. [Guard] Raise ContentIntegrityError if validation fails
  13. Generate diff (segments + full content)
  14. [Dry-run] Return result without publishing
  15. [Apply] Publish to WordPress
  16. Save backup + audit log
  17. Return ModificationResult
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.application.services.builder_detector import BuilderDetector
from src.application.services.divi_extractor import DiviExtractor
from src.application.services.rendered_html_extractor import RenderedHTMLExtractor
from src.domain.entities import (
    BuilderType,
    ExtractionMode,
    ExtractionReport,
    ModificationResult,
    ModificationStatus,
    PolicyDecision,
)
from src.domain.exceptions import ContentIntegrityError, LLMProviderError
from src.domain.ports import (
    IBackupRepository,
    IContentProtectionService,
    IDiffService,
    ILLMProvider,
    IWordPressClient,
)

logger = logging.getLogger(__name__)


class ModifyPageUseCase:
    """
    Caso de uso principal: modificación de contenido de una página WordPress.

    Orquesta el flujo completo sin conocer las implementaciones concretas.
    Todos los colaboradores son inyectados como interfaces (puertos).

    Flujo híbrido (post-refactor):
    ──────────────────────────────
    El extractor se selecciona dinámicamente según el builder detectado:

    - GUTENBERG / CLASSIC → ContentProtectionService (comportamiento original)
    - DIVI               → DiviExtractor (shortcode-aware)
    - ELEMENTOR / OXYGEN / BREAKDANCE / BRICKS → RenderedHTMLExtractor
      (analysis_only: extrae texto del HTML renderizado pero NO publica)
    - UNKNOWN            → analysis_only fallback

    La política de publicación es verificada antes del paso 15 (publish).
    Si publish está bloqueado y dry_run=False, el sistema lanza un error
    explícito en lugar de corromper la página.
    """

    def __init__(
        self,
        wp_client: IWordPressClient,
        llm_provider: ILLMProvider,
        protection_service: IContentProtectionService,
        backup_repo: IBackupRepository,
        diff_service: IDiffService,
    ) -> None:
        self._wp = wp_client
        self._llm = llm_provider
        self._protection = protection_service
        self._backup = backup_repo
        self._diff = diff_service
        # Servicios nuevos — sin inyectar como dependencia externa para no
        # romper el contrato del constructor existente (retrocompatibilidad).
        self._builder_detector = BuilderDetector()
        self._divi_extractor = DiviExtractor()
        self._rendered_extractor = RenderedHTMLExtractor()

    async def execute(
        self,
        identifier: str,
        instructions: str,
        dry_run: bool = True,
    ) -> ModificationResult:
        """
        Executes the full content modification pipeline.

        Args:
            identifier: URL, slug, or numeric ID of the WordPress page/post.
            instructions: Natural language instruction for the LLM.
            dry_run: If True, computes everything but does NOT publish to WordPress.

        Returns:
            ModificationResult with full operation details including ExtractionReport.

        Raises:
            WordPressPageNotFoundError: if the page doesn't exist.
            WordPressAuthError: if WP credentials are wrong.
            ContentIntegrityError: if the reconstructed HTML fails integrity checks,
                                   or if publishing is blocked for this builder.
            LLMProviderError: if the LLM returns an inconsistent segment count.
        """
        logger.info(
            "Starting page modification",
            extra={
                "identifier": identifier,
                "dry_run": dry_run,
                "instructions_preview": instructions[:80],
            },
        )

        # ── Step 1: Resolve identifier ─────────────────────────────────────────
        page_id = await self._wp.resolve_page_id(identifier)

        # ── Step 2: Fetch original page ────────────────────────────────────────
        page = await self._wp.get_page_by_id(page_id)

        logger.info(
            "Page fetched",
            extra={
                "page_id": page_id,
                "slug": page.slug,
                "content_length": len(page.raw_content),
                "content_type": page.content_type,
            },
        )

        # ── Step 3: Save backup BEFORE any processing ──────────────────────────
        backup_path = await self._backup.save_backup(
            page_id=page_id,
            original_content=page.raw_content,
            metadata={
                "instruction": instructions,
                "url": page.url,
                "slug": page.slug,
                "dry_run": dry_run,
            },
        )

        # ── Step 4: Detect builder ─────────────────────────────────────────────
        extraction_report = self._builder_detector.detect(page.raw_content)

        logger.info(
            "Builder detected",
            extra={
                "builder": extraction_report.builder_type.value,
                "confidence": extraction_report.confidence,
                "extraction_mode": extraction_report.extraction_mode.value,
                "policy": extraction_report.policy_decision.value,
                "publish_allowed": extraction_report.publish_allowed,
                "signals": extraction_report.detection_signals[:5],  # primeras 5 señales
            },
        )

        # ── Step 5: Extract segments (strategy depends on builder) ─────────────
        segments, original_content_for_diff, proposed_content, warnings = \
            await self._extract_and_process(
                page=page,
                extraction_report=extraction_report,
                instructions=instructions,
                dry_run=dry_run,
                backup_path=backup_path,
            )

        # Si _extract_and_process retornó None en proposed_content, significa
        # que ya construyó el ModificationResult completo (early exit o error).
        # En ese caso los "segments" contiene el resultado directamente.
        if isinstance(segments, ModificationResult):
            return segments  # type: ignore[return-value]

        # ── Step 6: Early exit if no editable content found ───────────────────
        if not segments:
            result = self._build_no_segments_result(
                page_id=page_id,
                page=page,
                instructions=instructions,
                dry_run=dry_run,
                backup_path=backup_path,
                extraction_report=extraction_report,
            )
            await self._save_audit_log(result)
            return result

        # ── Step 7: Policy check — block publish if not allowed ────────────────
        if not dry_run and not extraction_report.publish_allowed:
            raise ContentIntegrityError(
                f"Publishing is blocked for builder '{extraction_report.builder_type.value}'. "
                f"Reason: {extraction_report.publish_blocked_reason} "
                "Use dry_run=true to analyze and preview changes safely."
            )

        # ── Step 8: Transform segments via LLM ────────────────────────────────
        logger.info(
            "Sending segments to LLM",
            extra={"page_id": page_id, "segment_count": len(segments)},
        )
        modified_segments = await self._llm.transform_segments(segments, instructions)

        # ── Step 9: Guard — segment count integrity ────────────────────────────
        if len(modified_segments) != len(segments):
            raise LLMProviderError(
                f"LLM returned {len(modified_segments)} segments, "
                f"expected {len(segments)}. "
                "This is a provider contract violation."
            )

        # ── Step 10: Reconstruct content ───────────────────────────────────────
        new_content = self._reconstruct(
            extraction_report=extraction_report,
            page_raw_content=page.raw_content,
            modified_segments=modified_segments,
            # stored during extract step for Divi:
            _divi_protected=getattr(self, "_last_divi_protected", None),
            _standard_protected=getattr(self, "_last_standard_protected", None),
        )

        # ── Step 11: Validate structural integrity ──────────────────────────────
        # Para extractores analysis_only (Elementor, etc.), la validación
        # compara el texto extraído, no el HTML completo.
        token_map = getattr(self, "_last_token_map", {})
        validation = self._protection.validate_integrity(
            original_html=page.raw_content,
            reconstructed_html=new_content,
            token_map=token_map,
        )

        # ── Step 12: Guard — integrity failure → never publish ─────────────────
        if validation.has_errors():
            await self._backup.save_log({
                "event": "integrity_failure",
                "page_id": page_id,
                "page_url": page.url,
                "instruction": instructions,
                "errors": validation.errors,
                "missing_tokens": validation.missing_tokens,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.error(
                "Integrity validation failed — refusing to publish",
                extra={"page_id": page_id, "errors": validation.errors},
            )
            raise ContentIntegrityError(
                f"Structural integrity check failed with {len(validation.errors)} error(s): "
                f"{validation.errors}. Content was NOT published to protect the page."
            )

        # ── Step 13: Generate diffs ────────────────────────────────────────────
        segments_diff = self._diff.generate_segments_diff(segments, modified_segments)
        html_diff = self._diff.generate_diff(page.raw_content, new_content)

        changed_count = sum(
            1 for orig, mod in zip(segments, modified_segments)
            if orig.text != mod.get_final_text()
        )

        # ── Step 14 & 15: Publish or dry-run ───────────────────────────────────
        if dry_run:
            status = ModificationStatus.DRY_RUN
            logger.info(
                "Dry-run mode: changes computed but NOT published",
                extra={"page_id": page_id, "changed_segments": changed_count},
            )
        else:
            await self._wp.update_page(
                page_id=page_id,
                new_content=new_content,
                content_type=page.content_type,
            )
            status = ModificationStatus.SUCCESS
            logger.info(
                "Page published successfully",
                extra={"page_id": page_id, "changed_segments": changed_count},
            )

        # ── Step 16 & 17: Build result and save log ────────────────────────────
        all_warnings = list(warnings) + validation.warnings
        if extraction_report.policy_decision == PolicyDecision.ALLOW_WITH_CAUTION:
            all_warnings.append(
                f"Builder '{extraction_report.builder_type.value}' detected. "
                "Publish is allowed but changes were applied to builder shortcodes. "
                "Review the page visually after publishing."
            )

        result = ModificationResult(
            page_id=page_id,
            page_url=page.url,
            instruction=instructions,
            status=status,
            dry_run=dry_run,
            segments_found=len(segments),
            segments_modified=changed_count,
            diff_summary=segments_diff,
            backup_path=backup_path,
            original_content=page.raw_content,
            proposed_content=new_content,
            warnings=all_warnings,
            errors=[],
            extraction_report=extraction_report,
        )

        await self._save_audit_log(result, html_diff=html_diff)
        return result

    # ── Private: Extraction strategy dispatcher ───────────────────────────────

    async def _extract_and_process(
        self,
        page,
        extraction_report: ExtractionReport,
        instructions: str,
        dry_run: bool,
        backup_path: str,
    ):
        """
        Selecciona la estrategia de extracción según el builder detectado
        y extrae los segmentos.

        Returns (segments, original_for_diff, proposed_content, warnings)
        OR retorna (ModificationResult, None, None, None) en early-exit cases.
        """
        mode = extraction_report.extraction_mode
        warnings: list[str] = []

        if mode == ExtractionMode.STANDARD:
            # ── Gutenberg / Classic: pipeline original ───────────────────────
            protected = self._protection.extract_segments(page.raw_content)
            self._last_standard_protected = protected
            self._last_divi_protected = None
            self._last_token_map = protected.token_map
            return protected.segments, page.raw_content, None, warnings

        elif mode == ExtractionMode.DIVI_SHORTCODE:
            # ── Divi: extractor especializado de shortcodes ──────────────────
            divi_protected = self._divi_extractor.extract(page.raw_content)
            self._last_divi_protected = divi_protected
            self._last_standard_protected = None
            self._last_token_map = {}
            if divi_protected.has_global_modules:
                warnings.append(
                    "This Divi page contains Global Modules. "
                    "Edits to shared module titles will affect all pages using that module."
                )
            return divi_protected.segments, page.raw_content, None, warnings

        elif mode == ExtractionMode.RENDERED_HTML:
            # ── Elementor/Oxygen/Breakdance/Bricks: extraer del HTML público ─
            segments = await self._rendered_extractor.extract_from_url(page.url)
            self._last_standard_protected = None
            self._last_divi_protected = None
            self._last_token_map = {}
            warnings.append(
                f"Builder '{extraction_report.builder_type.value}' detected. "
                "Text was extracted from the rendered page HTML. "
                "Publishing is BLOCKED for this builder type — only dry_run analysis is available. "
                f"Reason: {extraction_report.publish_blocked_reason}"
            )
            return segments, page.raw_content, None, warnings

        else:
            # ── NONE / UNKNOWN: sin extracción posible ───────────────────────
            self._last_standard_protected = None
            self._last_divi_protected = None
            self._last_token_map = {}
            return [], page.raw_content, None, warnings

    def _reconstruct(
        self,
        extraction_report: ExtractionReport,
        page_raw_content: str,
        modified_segments,
        _divi_protected,
        _standard_protected,
    ) -> str:
        """Selecciona la estrategia de reconstrucción según el builder."""
        mode = extraction_report.extraction_mode

        if mode == ExtractionMode.DIVI_SHORTCODE and _divi_protected is not None:
            return self._divi_extractor.reconstruct(_divi_protected, modified_segments)

        elif mode == ExtractionMode.STANDARD and _standard_protected is not None:
            return self._protection.reconstruct(_standard_protected, modified_segments)

        else:
            # RENDERED_HTML y UNKNOWN no tienen reconstrucción —
            # retornar el contenido original intacto.
            # La validación de integridad pasará porque son idénticos.
            return page_raw_content

    # ── Private: Result builders ──────────────────────────────────────────────

    def _build_no_segments_result(
        self,
        page_id: int,
        page,
        instructions: str,
        dry_run: bool,
        backup_path: str,
        extraction_report: ExtractionReport,
    ) -> ModificationResult:
        """Construye el resultado cuando no se encontraron segmentos."""
        builder_name = extraction_report.builder_type.value

        if extraction_report.extraction_mode == ExtractionMode.RENDERED_HTML:
            warning_msg = (
                f"No editable text segments were found in the rendered HTML of this "
                f"{builder_name} page. The page may be heavily JavaScript-based or "
                "all visible text may be too short to be useful."
            )
        elif extraction_report.extraction_mode == ExtractionMode.DIVI_SHORTCODE:
            warning_msg = (
                "No editable text segments were found in this Divi page. "
                "The page may only contain Global Modules with no local text, "
                "or the editable text blocks may be empty."
            )
        else:
            warning_msg = (
                "No plain-text segments were found. The page may use complex inline "
                "HTML (e.g., bold/italic mixed with text) or contain no editable text."
            )

        logger.warning(
            "No editable segments found — returning without changes",
            extra={"page_id": page_id, "slug": page.slug, "builder": builder_name},
        )

        return ModificationResult(
            page_id=page_id,
            page_url=page.url,
            instruction=instructions,
            status=ModificationStatus.SUCCESS,
            dry_run=dry_run,
            segments_found=0,
            segments_modified=0,
            diff_summary="(no editable text segments found in this page)",
            backup_path=backup_path,
            original_content=page.raw_content,
            proposed_content=page.raw_content,
            warnings=[warning_msg],
            errors=[],
            extraction_report=extraction_report,
        )

    # ── Private: Audit logging ────────────────────────────────────────────────

    async def _save_audit_log(
        self,
        result: ModificationResult,
        html_diff: str = "",
    ) -> None:
        """Persists the audit record. Errors here are logged but NOT propagated."""
        try:
            await self._backup.save_log({
                "event": "modification",
                "page_id": result.page_id,
                "page_url": result.page_url,
                "instruction": result.instruction,
                "status": result.status.value,
                "dry_run": result.dry_run,
                "builder_detected": result.extraction_report.builder_type.value,
                "extraction_mode": result.extraction_report.extraction_mode.value,
                "confidence": result.extraction_report.confidence,
                "publish_allowed": result.extraction_report.publish_allowed,
                "segments_found": result.segments_found,
                "segments_modified": result.segments_modified,
                "backup_path": result.backup_path,
                "warnings": result.warnings,
                "html_diff_preview": html_diff[:500] if html_diff else "",
                "timestamp": result.created_at.isoformat(),
            })
        except Exception as log_error:
            logger.error(
                "Failed to save audit log (non-critical)",
                extra={"error": str(log_error)},
            )
