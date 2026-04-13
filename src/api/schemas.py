from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.domain.entities import ModificationResult


class ModifyPageRequest(BaseModel):
    """
    Request body para el endpoint POST /api/v1/modifications.

    identifier: URL pública, slug, o ID numérico de la página en WordPress.
    instructions: Instrucción en lenguaje natural para el LLM.
    dry_run: Si True (por defecto), calcula todo pero NO publica en WordPress.
    """

    identifier: str = Field(
        ...,
        min_length=1,
        description="URL, slug o ID numérico de la página WordPress a modificar.",
        examples=["https://tusitio.com/servicios/", "servicios", "42"],
    )
    instructions: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Instrucción en lenguaje natural para el LLM.",
        examples=[
            "Mejora el SEO de este contenido sin keyword stuffing.",
            "Humaniza el tono del texto manteniendo la intención comercial.",
            "Reescribe con mayor claridad y concisión.",
        ],
    )
    dry_run: bool = Field(
        default=True,
        description="Si True, devuelve el resultado sin publicar en WordPress.",
    )

    @field_validator("instructions", mode="before")
    @classmethod
    def instructions_must_not_be_blank(cls, v: str) -> str:
        """Reject whitespace-only instructions even if len >= min_length."""
        if isinstance(v, str) and not v.strip():
            raise ValueError("instructions must not be blank or whitespace only.")
        return v

    @field_validator("identifier", mode="before")
    @classmethod
    def identifier_must_not_be_blank(cls, v: str) -> str:
        """Reject whitespace-only identifiers."""
        if isinstance(v, str) and not v.strip():
            raise ValueError("identifier must not be blank or whitespace only.")
        return v



class ModifyPageResponse(BaseModel):
    """
    Response del endpoint POST /api/v1/modifications.

    Serialización directa de ModificationResult del dominio.
    original_content y proposed_content se incluyen para permitir
    review completo antes de publicar.

    Campos de detección de builder:
        builder_detected:     Qué builder fue identificado.
        extraction_mode:      Qué estrategia de extracción se usó.
        confidence:           Nivel de confianza de la detección (0.0-1.0).
        publish_allowed:      Si el sistema puede publicar cambios reales.
        publish_blocked_reason: Por qué está bloqueado (si aplica).
    """

    page_id: int
    page_url: str
    instruction: str
    status: str
    dry_run: bool
    segments_found: int
    segments_modified: int
    diff_summary: str
    backup_path: str
    original_content: str
    proposed_content: str
    warnings: list[str]
    errors: list[str]
    created_at: str
    # ── Builder detection fields ───────────────────────────────────────────────
    builder_detected: str = "unknown"
    extraction_mode: str = "none"
    confidence: float = 0.0
    publish_allowed: bool = False
    publish_blocked_reason: str = ""

    @classmethod
    def from_domain(cls, result: ModificationResult) -> ModifyPageResponse:
        """Converts a domain ModificationResult into an API response schema."""
        report = result.extraction_report
        return cls(
            page_id=result.page_id,
            page_url=result.page_url,
            instruction=result.instruction,
            status=result.status.value,
            dry_run=result.dry_run,
            segments_found=result.segments_found,
            segments_modified=result.segments_modified,
            diff_summary=result.diff_summary,
            backup_path=result.backup_path,
            original_content=result.original_content,
            proposed_content=result.proposed_content,
            warnings=result.warnings,
            errors=result.errors,
            created_at=result.created_at.isoformat(),
            builder_detected=report.builder_type.value,
            extraction_mode=report.extraction_mode.value,
            confidence=report.confidence,
            publish_allowed=report.publish_allowed,
            publish_blocked_reason=report.publish_blocked_reason,
        )



class ErrorResponse(BaseModel):
    """Schema estándar para responses de error."""
    detail: str
    error_type: str = "error"
