"""
divi_extractor.py — Extractor especializado para páginas Divi Builder.

Responsabilidad: extraer segmentos de texto editables desde el formato
de shortcodes de Divi sin romper su estructura.

Divi usa dos tipos de contenido textual:
  1. Atributo title= en et_pb_heading, et_pb_button, et_pb_accordion_item
  2. Contenido HTML en et_pb_text (entre los tags del shortcode)

Este extractor opera directamente sobre el string de shortcodes,
NO sobre HTML. BeautifulSoup no sirve aquí porque el input no es HTML.

Estrategia de reconstrucción:
  El extractor mapea cada segmento a su ubicación exacta en el string
  original usando offsets. La reconstrucción remplaza los valores en el
  string usando esos offsets, de afuera hacia adentro para no desplazar
  las posiciones de segmentos no procesados aún.

Limitación conocida:
  - et_pb_heading con title="" dentro de un Global Module (global_module="N")
    modifica el módulo compartido, no la página puntual.
  - El sistema marca esto como warning pero no bloquea la operación.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.domain.entities import EditableSegment

logger = logging.getLogger(__name__)

# ── Mínimo de caracteres para considerar un texto editable ────────────────────
MIN_TEXT_LENGTH = 15

# ── Tipos de segmento Divi ────────────────────────────────────────────────────
SEGMENT_TYPE_TITLE = "divi_title_attr"       # viene de title=""
SEGMENT_TYPE_TEXT = "divi_text_content"      # viene de [et_pb_text]...[/et_pb_text]
SEGMENT_TYPE_ACCORDION = "divi_accordion"    # viene de et_pb_accordion_item title=""


@dataclass
class DiviSegmentLocation:
    """
    Posición exacta de un segmento en el string de shortcodes Divi.
    Necesario para reconstruir el string con los textos modificados.
    """
    segment_index: int
    segment_type: str
    original_text: str
    # Para title= : posición del VALUE dentro del string (sin las comillas)
    # Para text content: posición del HTML interno al shortcode
    value_start: int
    value_end: int


@dataclass
class DiviProtectedContent:
    """
    Equivalente a ProtectedContent pero para Divi shortcodes.
    Almacena el string original y el mapa de segmentos localizados.
    """
    raw_shortcodes: str
    segments: list[EditableSegment]
    locations: list[DiviSegmentLocation]
    has_global_modules: bool = False


class DiviExtractor:
    """
    Extrae segmentos de texto de páginas construidas con Divi Builder.

    Caso de uso:
        extractor = DiviExtractor()
        protected = extractor.extract(raw_content)
        # protected.segments → lista de EditableSegment para el LLM
        # Después de LLM:
        new_html = extractor.reconstruct(protected, modified_segments)

    Garantías:
        - Los shortcodes Divi y sus atributos NO son tocados (salvo title=)
        - El HTML dentro de et_pb_text se modifica segmento a segmento
        - Los Global Modules se marcan en warnings pero se procesan
        - La reconstrucción aplica cambios de derecha a izquierda para
          preservar la validez de los offsets
    """

    # Pattern para et_pb_heading y et_pb_accordion_item con title=
    _RE_TITLE_ATTR = re.compile(
        r'\[(et_pb_heading|et_pb_accordion_item)\b([^\]]*?)\btitle\s*=\s*"([^"]*)"([^\]]*?)\]',
        re.DOTALL,
    )

    # Pattern para et_pb_button con button_text=
    _RE_BUTTON_TEXT = re.compile(
        r'\[et_pb_button\b([^\]]*?)\bbutton_text\s*=\s*"([^"]*)"([^\]]*?)\]',
        re.DOTALL,
    )

    # Pattern para et_pb_text: captura el contenido entre los shortcode tags
    _RE_TEXT_CONTENT = re.compile(
        r'\[et_pb_text\b[^\]]*\](.*?)\[/et_pb_text\]',
        re.DOTALL,
    )

    # Pattern para et_pb_accordion_item: captura el body entre los tags
    _RE_ACCORDION_BODY = re.compile(
        r'\[et_pb_accordion_item\b[^\]]*\](.*?)\[/et_pb_accordion_item\]',
        re.DOTALL,
    )

    # Detectar global_module= para advertir al usuario
    _RE_GLOBAL_MODULE = re.compile(r'\bglobal_module\s*=\s*"\d+"')

    # Detectar texto solo de tokens (shortcodes internos)
    _RE_ONLY_TOKEN = re.compile(r'^\s*\[.*?\]\s*$', re.DOTALL)

    def extract(self, raw_shortcodes: str) -> DiviProtectedContent:
        """
        Extrae todos los segmentos de texto editables del shortcode Divi.

        Returns:
            DiviProtectedContent con segmentos y sus posiciones exactas.
        """
        segments: list[EditableSegment] = []
        locations: list[DiviSegmentLocation] = []
        index = 0

        has_global = bool(self._RE_GLOBAL_MODULE.search(raw_shortcodes))
        if has_global:
            logger.warning(
                "Divi Global Modules detected. Edits to title= attributes in global "
                "modules will affect the shared module content, not just this page."
            )

        # ── Extraer title= de et_pb_heading y et_pb_accordion_item ───────────
        for match in self._RE_TITLE_ATTR.finditer(raw_shortcodes):
            module_type = match.group(1)
            title_text = match.group(3)

            if not title_text or len(title_text.strip()) < MIN_TEXT_LENGTH:
                continue

            # Encontrar la posición exacta del value dentro del string
            # match.group(3) es el texto sin comillas
            # Su posición en el string original:
            full_match = match.group(0)
            rel_pos = full_match.index(f'title="{title_text}"') + len('title="')
            abs_start = match.start() + rel_pos
            abs_end = abs_start + len(title_text)

            seg_type = (
                SEGMENT_TYPE_ACCORDION if module_type == "et_pb_accordion_item"
                else SEGMENT_TYPE_TITLE
            )

            segments.append(EditableSegment(
                index=index,
                tag=f"divi:{module_type}",
                text=title_text.strip(),
            ))
            locations.append(DiviSegmentLocation(
                segment_index=index,
                segment_type=seg_type,
                original_text=title_text,
                value_start=abs_start,
                value_end=abs_end,
            ))
            index += 1

        # ── Extraer button_text= de et_pb_button ─────────────────────────────
        for match in self._RE_BUTTON_TEXT.finditer(raw_shortcodes):
            btn_text = match.group(2)
            if not btn_text or len(btn_text.strip()) < 3:   # botones pueden ser cortos
                continue
            # Excluir textos que son solo iconos o placeholders
            if btn_text.strip().startswith("&#x"):
                continue

            full_match = match.group(0)
            rel_pos = full_match.index(f'button_text="{btn_text}"') + len('button_text="')
            abs_start = match.start() + rel_pos
            abs_end = abs_start + len(btn_text)

            segments.append(EditableSegment(
                index=index,
                tag="divi:et_pb_button",
                text=btn_text.strip(),
            ))
            locations.append(DiviSegmentLocation(
                segment_index=index,
                segment_type="divi_button_text",
                original_text=btn_text,
                value_start=abs_start,
                value_end=abs_end,
            ))
            index += 1

        # ── Extraer contenido de et_pb_text ───────────────────────────────────
        for match in self._RE_TEXT_CONTENT.finditer(raw_shortcodes):
            inner_html = match.group(1)
            clean_text = self._extract_plain_text(inner_html)

            if not clean_text or len(clean_text) < MIN_TEXT_LENGTH:
                continue

            # Posición del inner_html dentro del string original
            abs_start = match.start(1)
            abs_end = match.end(1)

            segments.append(EditableSegment(
                index=index,
                tag="divi:et_pb_text",
                text=clean_text,
            ))
            locations.append(DiviSegmentLocation(
                segment_index=index,
                segment_type=SEGMENT_TYPE_TEXT,
                original_text=inner_html,
                value_start=abs_start,
                value_end=abs_end,
            ))
            index += 1

        # ── Extraer body de et_pb_accordion_item ──────────────────────────────
        for match in self._RE_ACCORDION_BODY.finditer(raw_shortcodes):
            body_html = match.group(1)
            clean_text = self._extract_plain_text(body_html)

            if not clean_text or len(clean_text) < MIN_TEXT_LENGTH:
                continue

            abs_start = match.start(1)
            abs_end = match.end(1)

            segments.append(EditableSegment(
                index=index,
                tag="divi:et_pb_accordion_item_body",
                text=clean_text,
            ))
            locations.append(DiviSegmentLocation(
                segment_index=index,
                segment_type="divi_accordion_body",
                original_text=body_html,
                value_start=abs_start,
                value_end=abs_end,
            ))
            index += 1

        logger.info(
            "Divi extraction complete",
            extra={
                "segments_found": len(segments),
                "has_global_modules": has_global,
            },
        )

        return DiviProtectedContent(
            raw_shortcodes=raw_shortcodes,
            segments=segments,
            locations=locations,
            has_global_modules=has_global,
        )

    def reconstruct(
        self,
        protected: DiviProtectedContent,
        new_segments: list[EditableSegment],
    ) -> str:
        """
        Reemplaza los textos originales con los textos del LLM en el
        string de shortcodes Divi.

        Aplica los cambios de DERECHA a IZQUIERDA (mayor offset primero)
        para preservar la validez de los offsets de los segmentos restantes.

        Args:
            protected: El DiviProtectedContent retornado por extract().
            new_segments: Segmentos modificados por el LLM.

        Returns:
            El string de shortcodes con los textos actualizados.
        """
        # Construir mapa índice→segmento modificado
        modified_map = {seg.index: seg for seg in new_segments}
        result = protected.raw_shortcodes

        # Ordenar por offset descendente para aplicar de derecha a izquierda
        sorted_locations = sorted(
            protected.locations,
            key=lambda loc: loc.value_start,
            reverse=True,
        )

        for loc in sorted_locations:
            seg = modified_map.get(loc.segment_index)
            if seg is None:
                continue

            new_text = seg.get_final_text()

            # Para segmentos de tipo text/accordion_body: reconstruir el
            # HTML original reemplazando solo el texto visible
            if loc.segment_type in (SEGMENT_TYPE_TEXT, "divi_accordion_body"):
                # El LLM devuelve texto limpio; lo envolvemos en <p> si era así
                original = loc.original_text
                if "<p>" in original or "<p " in original:
                    new_inner = f"<p>{new_text}</p>"
                else:
                    new_inner = new_text
                result = result[:loc.value_start] + new_inner + result[loc.value_end:]
            else:
                # Para title= y button_text=: reemplazar valor exacto
                result = result[:loc.value_start] + new_text + result[loc.value_end:]

        return result

    @staticmethod
    def _extract_plain_text(html: str) -> str:
        """
        Extrae texto legible de un fragmento HTML (para comparación y
        exposición al LLM).
        Elimina tags HTML pero preserva el texto de dentro.
        """
        # Eliminar tags HTML
        text = re.sub(r'<[^>]+>', ' ', html)
        # Colapsar whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
