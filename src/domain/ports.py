from __future__ import annotations

from typing import Protocol

from src.domain.entities import (
    EditableSegment,
    PageContent,
    ProtectedContent,
    ValidationResult,
)


class IWordPressClient(Protocol):
    """
    Puerto de salida: acceso a WordPress.
    Responsabilidades separadas por método — no hay método que mezcle resolver + obtener.
    """

    async def resolve_page_id(self, identifier: str) -> int:
        """
        Resuelve un identificador (URL pública, slug, o ID numérico como string)
        al ID interno de WordPress.
        Lanza WordPressPageNotFoundError si no se puede resolver.
        """
        ...

    async def get_page_by_id(self, page_id: int) -> PageContent:
        """
        Obtiene el contenido completo de una página por su ID numérico.
        Usa context=edit para obtener el contenido RAW (sin filtros WP).
        Lanza WordPressPageNotFoundError si la página no existe.
        Lanza WordPressAuthError si las credenciales son incorrectas.
        """
        ...

    async def update_page(
        self,
        page_id: int,
        new_content: str,
        content_type: str = "page",
    ) -> bool:
        """
        Publica el nuevo contenido en WordPress.
        content_type: "page" o "post" — evita un GET adicional de verificación.
        Devuelve True si la actualización fue exitosa.
        """
        ...


class ILLMProvider(Protocol):
    """
    Puerto de salida: proveedor de modelo de lenguaje.

    CONTRATO DELIBERADO: opera con lista ordenada de EditableSegment.
    Se rechaza firma simplista (str -> str) porque:
    - Perderíamos el orden y la identidad de cada segmento.
    - El LLM podría colapsar o inventar párrafos sin que lo detectemos.
    - Este contrato nos permite validar count(in) == count(out).
    """

    async def transform_segments(
        self,
        segments: list[EditableSegment],
        instructions: str,
    ) -> list[EditableSegment]:
        """
        Transforma una lista ordenada de segmentos editables según las instrucciones.
        Garantía del implementador: len(resultado) == len(segments).
        El índice de cada segmento resultado debe corresponder al input.
        """
        ...


class IContentProtectionService(Protocol):
    """
    Puerto interno: servicio de protección estructural.
    Este es el componente más crítico del sistema.
    """

    def extract_segments(self, raw_html: str) -> ProtectedContent:
        """
        1. Detecta y tokeniza elementos protegidos (shortcodes, bloques Gutenberg,
           scripts, iframes, formularios).
        2. Extrae nodos de texto editable del HTML resultante.
        3. Devuelve ProtectedContent con el mapa de tokens y los segmentos.
        """
        ...

    def reconstruct(
        self,
        protected: ProtectedContent,
        new_segments: list[EditableSegment],
    ) -> str:
        """
        1. Reemplaza el texto de cada segment en el tokenized_html.
        2. Restaura todos los tokens protegidos a sus valores exactos originales.
        3. Devuelve el HTML final.
        """
        ...

    def validate_integrity(
        self,
        original_html: str,
        reconstructed_html: str,
        token_map: dict[str, str],
    ) -> ValidationResult:
        """
        Valida preservación funcional (NO identidad byte a byte).
        Comprueba: tokens presentes, longitud razonable, ausencia de errores críticos.
        """
        ...


class IBackupRepository(Protocol):
    """
    Puerto de salida: almacenamiento de backups y registros de auditoría.
    """

    async def save_backup(
        self,
        page_id: int,
        original_content: str,
        metadata: dict,
    ) -> str:
        """
        Persiste el contenido original antes de cualquier modificación.
        Devuelve la ruta o referencia al backup creado.
        """
        ...

    async def save_log(self, record: dict) -> None:
        """Persiste el registro de auditoría de la operación."""
        ...


class IDiffService(Protocol):
    """Puerto interno: generación de diff textual legible."""

    def generate_diff(self, original: str, modified: str) -> str:
        """Genera un diff unificado legible entre texto original y modificado."""
        ...

    def generate_segments_diff(
        self,
        original_segments: list[EditableSegment],
        modified_segments: list[EditableSegment],
    ) -> str:
        """
        Genera un resumen comparativo segmento a segmento.
        Solo incluye los segmentos que cambiaron.
        """
        ...
