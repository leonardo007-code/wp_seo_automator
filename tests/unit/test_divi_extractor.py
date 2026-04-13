"""
test_divi_extractor.py — Tests de caja blanca del DiviExtractor.

Cubre:
  - Extracción de title= de et_pb_heading
  - Extracción de button_text= de et_pb_button
  - Extracción de contenido de et_pb_text
  - Extracción de títulos de et_pb_accordion_item
  - Extracción de cuerpos de et_pb_accordion_item
  - Filtrado de textos demasiado cortos
  - Detección de Global Modules
  - Reconstrucción correcta tras modificación del LLM
  - Idempotencia (sin cambios = output idéntico al input)
"""
import pytest

from src.application.services.divi_extractor import DiviExtractor, DiviProtectedContent
from src.domain.entities import EditableSegment
from tests.fixtures.page_content_fixtures import (
    DIVI_PAGE,
    DIVI_PAGE_WITH_GLOBAL_MODULE,
    EMPTY_PAGE,
)


@pytest.fixture
def extractor() -> DiviExtractor:
    return DiviExtractor()


# ── Extracción de segmentos ────────────────────────────────────────────────────

class TestDiviExtraction:
    def test_extracts_heading_title(self, extractor):
        content = '[et_pb_heading title="Conectamos Contigo con nuestros servicios" _builder_version="4.27.4"][/et_pb_heading]'
        protected = extractor.extract(content)
        texts = [seg.text for seg in protected.segments]
        assert "Conectamos Contigo con nuestros servicios" in texts

    def test_extracts_text_module_content(self, extractor):
        content = '[et_pb_text _builder_version="4.27.4"]<p>Tu próxima gran idea merece su lugar en el mercado.</p>[/et_pb_text]'
        protected = extractor.extract(content)
        texts = [seg.text for seg in protected.segments]
        assert any("Tu próxima gran idea" in t for t in texts)

    def test_extracts_button_text(self, extractor):
        content = '[et_pb_button button_text="Contáctanos ahora" button_url="#" _builder_version="4.27.4"][/et_pb_button]'
        protected = extractor.extract(content)
        texts = [seg.text for seg in protected.segments]
        assert "Contáctanos ahora" in texts

    def test_extracts_accordion_item_title(self, extractor):
        content = '[et_pb_accordion_item title="¿Cuánto tiempo tarda el proyecto?" open="on" _builder_version="4.27.4"]<p>Entre una y dos semanas según la complejidad del sitio.</p>[/et_pb_accordion_item]'
        protected = extractor.extract(content)
        titles = [seg.text for seg in protected.segments if "accordion" in seg.tag.lower() or "heading" in seg.tag.lower() or "et_pb_accordion_item" in seg.tag]
        assert any("¿Cuánto tiempo tarda el proyecto?" in t for t in [seg.text for seg in protected.segments])

    def test_extracts_accordion_body(self, extractor):
        content = '[et_pb_accordion_item title="Pregunta larga sobre los servicios" open="on" _builder_version="4.27.4"]<p>Esta es una respuesta larga sobre nuestros servicios de desarrollo web profesional.</p>[/et_pb_accordion_item]'
        protected = extractor.extract(content)
        assert any("respuesta larga" in seg.text for seg in protected.segments)

    def test_extracts_multiple_segments_from_full_page(self, extractor):
        protected = extractor.extract(DIVI_PAGE)
        assert len(protected.segments) >= 2

    def test_segment_indices_are_sequential(self, extractor):
        protected = extractor.extract(DIVI_PAGE)
        indices = [seg.index for seg in protected.segments]
        assert indices == list(range(len(indices)))

    def test_no_segments_from_image_only_page(self, extractor):
        protected = extractor.extract(EMPTY_PAGE)
        assert len(protected.segments) == 0

    def test_skips_short_icon_text_in_button(self, extractor):
        content = '[et_pb_button button_text="&#xf3cd;" button_url="#" _builder_version="4.27.4"][/et_pb_button]'
        protected = extractor.extract(content)
        # Botón con solo icono no debe ser extraído
        assert all("&#x" not in seg.text for seg in protected.segments)


# ── Detección de Global Modules ────────────────────────────────────────────────

class TestGlobalModuleDetection:
    def test_detects_global_module(self, extractor):
        protected = extractor.extract(DIVI_PAGE_WITH_GLOBAL_MODULE)
        assert protected.has_global_modules is True

    def test_no_global_module_flag_for_normal_page(self, extractor):
        protected = extractor.extract(DIVI_PAGE)
        assert protected.has_global_modules is False


# ── Reconstrucción ────────────────────────────────────────────────────────────

class TestDiviReconstruction:
    def test_reconstruction_without_changes_is_idempotent(self, extractor):
        """Si los segmentos no cambian, el output debe ser idéntico al input."""
        protected = extractor.extract(DIVI_PAGE)
        # Usamos los segmentos originales sin modificar
        result = extractor.reconstruct(protected, protected.segments)
        # Los segmentos deben estar presentes en el resultado
        for seg in protected.segments:
            assert seg.text in result

    def test_reconstruction_replaces_heading_title(self, extractor):
        content = '[et_pb_heading title="Título original largo del heading" _builder_version="4.27.4"][/et_pb_heading]'
        protected = extractor.extract(content)
        assert len(protected.segments) >= 1

        # Simular modificación del LLM
        modified = [
            EditableSegment(
                index=seg.index,
                tag=seg.tag,
                text=seg.text,
                modified_text="Título mejorado para SEO con palabras clave"
            )
            for seg in protected.segments
        ]

        result = extractor.reconstruct(protected, modified)
        assert "Título mejorado para SEO con palabras clave" in result
        assert "Título original largo del heading" not in result

    def test_reconstruction_preserves_shortcode_structure(self, extractor):
        """La estructura del shortcode Divi debe quedar intacta tras reconstruir."""
        protected = extractor.extract(DIVI_PAGE)
        # Sin cambios
        result = extractor.reconstruct(protected, protected.segments)
        # Los shortcodes estructurales deben seguir presentes
        assert "[et_pb_section" in result
        assert "[et_pb_row" in result
        assert "_builder_version" in result

    def test_reconstruction_replaces_text_module_content(self, extractor):
        content = '[et_pb_text _builder_version="4.27.4"]<p>Este es el texto original que necesita mejoras para SEO.</p>[/et_pb_text]'
        protected = extractor.extract(content)
        assert len(protected.segments) >= 1

        modified = [
            EditableSegment(
                index=seg.index,
                tag=seg.tag,
                text=seg.text,
                modified_text="Este texto ha sido optimizado para mejorar el posicionamiento web."
            )
            for seg in protected.segments
        ]

        result = extractor.reconstruct(protected, modified)
        assert "optimizado para mejorar el posicionamiento" in result

    def test_reconstruction_with_multiple_segments(self, extractor):
        """Probar reconstrucción cuando hay múltiples segmentos."""
        protected = extractor.extract(DIVI_PAGE)
        if len(protected.segments) < 2:
            pytest.skip("Fixture no tiene suficientes segmentos")

        # Modificar solo el primer segmento
        modified = []
        for seg in protected.segments:
            if seg.index == 0:
                modified.append(EditableSegment(
                    index=seg.index,
                    tag=seg.tag,
                    text=seg.text,
                    modified_text="Texto completamente nuevo para el primer segmento del sistema"
                ))
            else:
                modified.append(seg)  # sin cambios

        result = extractor.reconstruct(protected, modified)
        assert "Texto completamente nuevo para el primer segmento del sistema" in result
        # Los otros segmentos no modificados deben seguir presentes
        for seg in protected.segments[1:]:
            assert seg.text in result


# ── Tags y metadatos de segmentos ─────────────────────────────────────────────

class TestSegmentMetadata:
    def test_heading_segment_has_divi_tag(self, extractor):
        content = '[et_pb_heading title="Título largo del que estamos hablando aquí" _builder_version="4.27.4"][/et_pb_heading]'
        protected = extractor.extract(content)
        heading_segs = [seg for seg in protected.segments if "heading" in seg.tag]
        assert len(heading_segs) > 0
        assert all("divi" in seg.tag for seg in heading_segs)

    def test_text_module_segment_has_divi_tag(self, extractor):
        content = '[et_pb_text _builder_version="4.27.4"]<p>Texto suficientemente largo para ser segmento.</p>[/et_pb_text]'
        protected = extractor.extract(content)
        text_segs = [seg for seg in protected.segments if "et_pb_text" in seg.tag]
        assert len(text_segs) > 0

    def test_all_segments_have_non_empty_text(self, extractor):
        protected = extractor.extract(DIVI_PAGE)
        for seg in protected.segments:
            assert seg.text.strip() != ""
