from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ModificationStatus(str, Enum):
    """Estado del ciclo de vida de una modificación."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    DRY_RUN = "dry_run"


@dataclass
class EditableSegment:
    """
    Representa un fragmento de texto que el LLM puede modificar.

    index: posición original en el documento (preserva el orden)
    tag: etiqueta HTML de origen (p, h2, li, etc.)
    text: contenido textual extraído
    modified_text: contenido generado por el LLM (None si aún no fue procesado)
    """
    index: int
    tag: str
    text: str
    modified_text: str | None = None

    def has_been_modified(self) -> bool:
        return self.modified_text is not None

    def get_final_text(self) -> str:
        return self.modified_text if self.modified_text is not None else self.text


@dataclass
class ProtectedContent:
    """
    Resultado del proceso de extracción segura.

    raw_html: HTML original recibido de WordPress
    token_map: dict de token_placeholder -> valor_original_exacto (shortcodes, bloques, scripts)
    segments: lista ordenada de segmentos editables extraídos
    tokenized_html: HTML con elementos protegidos reemplazados por tokens
    """
    raw_html: str
    token_map: dict[str, str]
    segments: list[EditableSegment]
    tokenized_html: str


@dataclass
class ValidationResult:
    """
    Resultado de la validación de integridad estructural post-modificación.
    No valida identidad byte a byte, sino preservación funcional.
    """
    is_valid: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    missing_tokens: list[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        return len(self.errors) > 0


@dataclass
class PageContent:
    """
    Representación de una página de WordPress dentro del dominio.
    No es el response crudo de la API — es el modelo de negocio.

    content_type: "page" o "post" — necesario para saber a qué endpoint
    enviar las actualizaciones sin un lookup adicional.
    """
    page_id: int
    slug: str
    title: str
    raw_content: str
    url: str
    last_modified: str
    content_type: str = "page"


@dataclass
class ModificationRecord:
    """
    Registro de auditoría de una operación de modificación.
    Este objeto se persiste en logs y backups.
    """
    page_id: int
    page_url: str
    instruction: str
    status: ModificationStatus
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    original_content: str = ""
    proposed_content: str = ""
    diff_summary: str = ""
    backup_path: str = ""
    error_message: str = ""
    dry_run: bool = True


@dataclass
class ModificationResult:
    """
    Objeto de respuesta del ModifyPageUseCase.

    Este es el contrato de salida del caso de uso.
    La capa API lo serializa directamente en el response HTTP.

    Separado de ModificationRecord: ese es para auditoría interna,
    este es para comunicar el resultado al caller.
    """
    page_id: int
    page_url: str
    instruction: str
    status: ModificationStatus
    dry_run: bool
    segments_found: int
    segments_modified: int
    diff_summary: str
    backup_path: str
    original_content: str
    proposed_content: str
    warnings: list[str]
    errors: list[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

