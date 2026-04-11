from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.domain.entities import (
    ModificationResult,
    ModificationStatus,
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

    Flujo:
    ──────
    1.  Resolve identifier → page_id
    2.  Fetch page by ID → PageContent (raw HTML)
    3.  Save backup (ANTES de cualquier cambio, siempre)
    4.  Extract editable segments → ProtectedContent
    5.  [Early exit] if no segments found
    6.  Transform segments via LLM → modified EditableSegments
    7.  [Guard] Validate segment count matches
    8.  Reconstruct HTML with modified text + restored tokens
    9.  Validate structural integrity → ValidationResult
    10. [Guard] Raise ContentIntegrityError if validation fails
    11. Generate diff (segments + full HTML)
    12. [Dry-run] Return result without publishing
    13. [Apply] Publish to WordPress
    14. Save audit log
    15. Return ModificationResult
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
            ModificationResult with full operation details.

        Raises:
            WordPressPageNotFoundError: if the page doesn't exist.
            WordPressAuthError: if WP credentials are wrong.
            ContentIntegrityError: if the reconstructed HTML fails integrity checks.
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
        # We backup even in dry-run: we want a record of what the page looked like
        # at the time of every analysis, not just on actual publishes.
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

        # ── Step 4: Extract editable segments ──────────────────────────────────
        protected = self._protection.extract_segments(page.raw_content)

        # ── Step 5: Early exit if no editable content found ───────────────────
        if not protected.segments:
            logger.warning(
                "No editable segments found — returning without changes",
                extra={"page_id": page_id, "slug": page.slug},
            )
            result = ModificationResult(
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
                warnings=[
                    "No plain-text segments were found. The page may use complex inline "
                    "HTML (e.g., bold/italic mixed with text) or contain no editable text."
                ],
                errors=[],
            )
            await self._save_audit_log(result)
            return result

        # ── Step 6: Transform segments via LLM ────────────────────────────────
        logger.info(
            "Sending segments to LLM",
            extra={
                "page_id": page_id,
                "segment_count": len(protected.segments),
            },
        )
        modified_segments = await self._llm.transform_segments(
            protected.segments, instructions
        )

        # ── Step 7: Guard — segment count integrity ────────────────────────────
        # GeminiProvider already guarantees this, but we enforce it at the use case
        # level too. If a future LLM provider breaks the contract, we catch it here.
        if len(modified_segments) != len(protected.segments):
            raise LLMProviderError(
                f"LLM returned {len(modified_segments)} segments, "
                f"expected {len(protected.segments)}. "
                "This is a provider contract violation."
            )

        # ── Step 8: Reconstruct HTML ───────────────────────────────────────────
        new_html = self._protection.reconstruct(protected, modified_segments)

        # ── Step 9: Validate structural integrity ──────────────────────────────
        validation = self._protection.validate_integrity(
            original_html=page.raw_content,
            reconstructed_html=new_html,
            token_map=protected.token_map,
        )

        # ── Step 10: Guard — integrity failure → never publish ─────────────────
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

        # ── Step 11: Generate diffs ────────────────────────────────────────────
        # Segment-level diff: human-readable, for the API response
        segments_diff = self._diff.generate_segments_diff(
            protected.segments, modified_segments
        )
        # Full HTML diff: for detailed audit logs
        html_diff = self._diff.generate_diff(page.raw_content, new_html)

        # Count actual changed segments
        changed_count = sum(
            1
            for orig, mod in zip(protected.segments, modified_segments)
            if orig.text != mod.get_final_text()
        )

        # ── Step 12 & 13: Publish or dry-run ──────────────────────────────────
        if dry_run:
            status = ModificationStatus.DRY_RUN
            logger.info(
                "Dry-run mode: changes computed but NOT published",
                extra={"page_id": page_id, "changed_segments": changed_count},
            )
        else:
            await self._wp.update_page(
                page_id=page_id,
                new_content=new_html,
                content_type=page.content_type,
            )
            status = ModificationStatus.SUCCESS
            logger.info(
                "Page published successfully",
                extra={"page_id": page_id, "changed_segments": changed_count},
            )

        # ── Step 14 & 15: Build result and save log ────────────────────────────
        result = ModificationResult(
            page_id=page_id,
            page_url=page.url,
            instruction=instructions,
            status=status,
            dry_run=dry_run,
            segments_found=len(protected.segments),
            segments_modified=changed_count,
            diff_summary=segments_diff,
            backup_path=backup_path,
            original_content=page.raw_content,
            proposed_content=new_html,
            warnings=validation.warnings,
            errors=[],
        )

        await self._save_audit_log(result, html_diff=html_diff)
        return result

    # ── Private helpers ────────────────────────────────────────────────────────

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
                "segments_found": result.segments_found,
                "segments_modified": result.segments_modified,
                "backup_path": result.backup_path,
                "warnings": result.warnings,
                "html_diff_preview": html_diff[:500] if html_diff else "",
                "timestamp": result.created_at.isoformat(),
            })
        except Exception as log_error:
            # Log errors must NEVER crash the main operation
            logger.error(
                "Failed to save audit log (non-critical)",
                extra={"error": str(log_error)},
            )
