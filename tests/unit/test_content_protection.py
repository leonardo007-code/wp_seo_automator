"""
Tests unitarios para ContentProtectionService.

Filosofía: testear contratos, no implementación.
Cada test verifica una garantía observable del servicio.
No se mockea BeautifulSoup ni regex internos — son detalles de implementación.
"""

from __future__ import annotations

import pytest

from src.application.services.content_protection import ContentProtectionService
from src.domain.entities import EditableSegment


@pytest.fixture
def service() -> ContentProtectionService:
    return ContentProtectionService()


# ── Fixtures de HTML realistas ─────────────────────────────────────────────────

SIMPLE_HTML = """
<article>
    <h2>Servicios de consultoría empresarial</h2>
    <p>Ofrecemos soluciones integrales para tu negocio con más de 20 años de experiencia.</p>
    <p>Nuestro equipo está formado por expertos en estrategia, finanzas y operaciones.</p>
    <ul>
        <li>Consultoría estratégica</li>
        <li>Transformación digital</li>
    </ul>
</article>
"""

HTML_WITH_SHORTCODES = """
<div class="entry-content">
    <h2>Contáctanos hoy mismo para más información</h2>
    <p>Estamos disponibles para resolver todas tus dudas de manera personalizada.</p>
    [contact-form-7 id="123" title="Formulario de contacto"]
    <p>También puedes llamarnos en horario de oficina de lunes a viernes.</p>
</div>
"""

HTML_WITH_GUTENBERG = """
<!-- wp:group {"layout":{"type":"constrained"}} -->
<div class="wp-block-group">
<!-- wp:heading -->
<h2 class="wp-block-heading">Nuestros productos destacados del mes</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>Descubre nuestra selección exclusiva con los mejores precios del mercado.</p>
<!-- /wp:paragraph -->
<!-- wp:gallery {"columns":3} -->
<figure class="wp-block-gallery">
    <img src="producto1.jpg" alt="Producto 1" />
</figure>
<!-- /wp:gallery -->
</div>
<!-- /wp:group -->
"""

HTML_WITH_SCRIPT_AND_IFRAME = """
<section>
    <h2>Visítanos en nuestra nueva sede principal</h2>
    <p>Estamos ubicados en el centro de la ciudad, fácil acceso en transporte público.</p>
    <script>
        (function(w,d,s,l,i){ /* GTM snippet */ })(window,document);
    </script>
    <iframe
        src="https://www.google.com/maps/embed?pb=..."
        width="600"
        height="450"
        style="border:0;"
        allowfullscreen>
    </iframe>
</section>
"""

HTML_WITH_NESTED_INLINE = """
<div>
    <p>Texto simple que debe ser extraído correctamente por el sistema.</p>
    <p>Este párrafo tiene <strong>texto en negrita</strong> y debe ser omitido.</p>
    <h3>Encabezado limpio sin elementos inline</h3>
</div>
"""


# ── Tests de Tokenización ──────────────────────────────────────────────────────

class TestTokenization:

    def test_shortcode_is_tokenized_not_in_segments(self, service):
        """
        Los shortcodes NO deben aparecer como segmentos editables.
        Deben estar en token_map y ausentes del HTML segmentado como texto literal.
        """
        result = service.extract_segments(HTML_WITH_SHORTCODES)

        shortcode_literal = '[contact-form-7 id="123" title="Formulario de contacto"]'
        assert shortcode_literal not in result.tokenized_html
        assert any(
            shortcode_literal in v for v in result.token_map.values()
        ), "El shortcode debe estar almacenado en token_map"

    def test_script_tag_is_tokenized(self, service):
        """Los bloques <script> nunca deben aparecer en el HTML segmentado."""
        result = service.extract_segments(HTML_WITH_SCRIPT_AND_IFRAME)

        assert "<script>" not in result.tokenized_html
        assert any("GTM snippet" in v for v in result.token_map.values()), (
            "El contenido del script debe estar en token_map"
        )

    def test_iframe_is_tokenized(self, service):
        """Los iframes nunca deben aparecer en el HTML segmentado."""
        result = service.extract_segments(HTML_WITH_SCRIPT_AND_IFRAME)

        assert "google.com/maps" not in result.tokenized_html
        assert any("google.com/maps" in v for v in result.token_map.values())

    def test_gutenberg_block_markers_are_tokenized(self, service):
        """
        Los comentarios de bloques Gutenberg <!-- wp:* --> deben ser tokenizados.
        El CONTENIDO INTERNO (h2, p) debe seguir siendo accesible para edición.
        """
        result = service.extract_segments(HTML_WITH_GUTENBERG)

        # Los marcadores no deben estar en el HTML resultante
        assert "<!-- wp:group" not in result.tokenized_html
        assert "<!-- /wp:group" not in result.tokenized_html
        # Pero el texto editable sí debe seguir disponible
        assert len(result.segments) > 0, (
            "Debe haber segmentos editables dentro de los bloques Gutenberg"
        )

    def test_multiple_protected_elements_get_unique_tokens(self, service):
        """Cada elemento protegido debe tener su propio token único."""
        html = """
        <div>
            [shortcode_a]
            <p>Texto editable para verificar el sistema.</p>
            [shortcode_b]
        </div>
        """
        result = service.extract_segments(html)

        tokens = list(result.token_map.keys())
        assert len(tokens) == len(set(tokens)), "Los tokens deben ser únicos"
        assert len(tokens) >= 2, "Debe haber al menos 2 tokens para los 2 shortcodes"


# ── Tests de Extracción de Segmentos ──────────────────────────────────────────

class TestSegmentExtraction:

    def test_pure_text_paragraphs_are_extracted(self, service):
        result = service.extract_segments(SIMPLE_HTML)

        texts = [s.text for s in result.segments]
        assert any("soluciones integrales" in t for t in texts)
        assert any("20 años de experiencia" in t for t in texts)

    def test_heading_is_extracted(self, service):
        result = service.extract_segments(SIMPLE_HTML)

        tags = [s.tag for s in result.segments]
        assert "h2" in tags, "Los encabezados deben ser extraídos como segmentos"

    def test_list_items_are_extracted(self, service):
        result = service.extract_segments(SIMPLE_HTML)

        li_segments = [s for s in result.segments if s.tag == "li"]
        assert len(li_segments) >= 2

    def test_segments_have_correct_order(self, service):
        result = service.extract_segments(SIMPLE_HTML)

        indices = [s.index for s in result.segments]
        assert indices == list(range(len(indices))), (
            "Los segmentos deben tener índices consecutivos desde 0"
        )

    def test_inline_mixed_content_is_skipped(self, service):
        """
        Un <p> con <strong> interno no debe ser extraído.
        element.string devuelve None en ese caso — es nuestra señal de skip.
        """
        result = service.extract_segments(HTML_WITH_NESTED_INLINE)

        texts = [s.text for s in result.segments]
        assert not any("negrita" in t for t in texts), (
            "El párrafo con <strong> no debe extraerse"
        )
        # Pero el párrafo limpio sí
        assert any("debe ser extraído correctamente" in t for t in texts)

    def test_short_text_is_not_extracted(self, service):
        """Textos muy cortos (< MIN_TEXT_LENGTH) no son segmentos editables."""
        html = "<p>Corto</p><p>Este párrafo sí tiene suficiente texto para ser extraído.</p>"
        result = service.extract_segments(html)

        texts = [s.text for s in result.segments]
        assert "Corto" not in texts
        assert any("suficiente texto" in t for t in texts)

    def test_segments_stored_in_tokenized_html_as_placeholders(self, service):
        """El texto original no debe aparecer en tokenized_html — solo los tokens SEG."""
        result = service.extract_segments(SIMPLE_HTML)

        for segment in result.segments:
            seg_token = f"⟦SEG_{segment.index}⟧"
            assert seg_token in result.tokenized_html, (
                f"Token {seg_token!r} debe estar en tokenized_html"
            )
            # El texto original debe haber sido reemplazado
            assert segment.text not in result.tokenized_html, (
                f"El texto original {segment.text!r} no debe estar en tokenized_html"
            )


# ── Tests de Reconstrucción ────────────────────────────────────────────────────

class TestReconstruction:

    def test_reconstruction_without_modifications_reproduces_content(self, service):
        """
        Si no se modifica ningún segmento, la reconstrucción debe contener
        todos los textos originales y todos los elementos protegidos.
        """
        result = service.extract_segments(HTML_WITH_SHORTCODES)
        # Simular que el LLM no modificó nada
        reconstructed = service.reconstruct(result, result.segments)

        for seg in result.segments:
            assert seg.text in reconstructed, (
                f"El texto original {seg.text!r} debe estar en la reconstrucción"
            )

        shortcode = '[contact-form-7 id="123" title="Formulario de contacto"]'
        assert shortcode in reconstructed, "El shortcode debe ser restaurado exactamente"

    def test_modified_text_appears_in_reconstruction(self, service):
        """El texto modificado por el LLM debe reemplazar el original en la salida."""
        result = service.extract_segments(SIMPLE_HTML)

        modified_segments = []
        for seg in result.segments:
            modified = EditableSegment(
                index=seg.index,
                tag=seg.tag,
                text=seg.text,
                modified_text=f"[MODIFICADO] {seg.text}",
            )
            modified_segments.append(modified)

        reconstructed = service.reconstruct(result, modified_segments)

        for mod_seg in modified_segments:
            assert f"[MODIFICADO] {mod_seg.text}" in reconstructed

    def test_gutenberg_markers_restored_exactly(self, service):
        """Los marcadores de bloque Gutenberg deben ser restaurados byte a byte."""
        result = service.extract_segments(HTML_WITH_GUTENBERG)
        reconstructed = service.reconstruct(result, result.segments)

        assert '<!-- wp:group {"layout":{"type":"constrained"}} -->' in reconstructed
        assert "<!-- /wp:group -->" in reconstructed
        assert '<!-- wp:gallery {"columns":3} -->' in reconstructed

    def test_script_content_restored_exactly(self, service):
        """El contenido de <script> debe ser restaurado exactamente."""
        result = service.extract_segments(HTML_WITH_SCRIPT_AND_IFRAME)
        reconstructed = service.reconstruct(result, result.segments)

        assert "GTM snippet" in reconstructed
        assert "<script>" in reconstructed

    def test_llm_injected_token_markers_are_sanitized(self, service):
        """
        Si el LLM devuelve accidentalmente nuestros marcadores ⟦⟧,
        deben ser eliminados antes de la reconstrucción.
        """
        result = service.extract_segments(SIMPLE_HTML)

        malicious_segments = []
        for seg in result.segments:
            malicious_segments.append(
                EditableSegment(
                    index=seg.index,
                    tag=seg.tag,
                    text=seg.text,
                    modified_text=f"⟦WP_999⟧ texto inyectado ⟦SEG_0⟧",
                )
            )

        reconstructed = service.reconstruct(result, malicious_segments)

        assert "⟦WP_999⟧" not in reconstructed
        assert "⟦SEG_0⟧" not in reconstructed


# ── Tests de Validación de Integridad ─────────────────────────────────────────

class TestValidateIntegrity:

    def test_valid_reconstruction_passes(self, service):
        result = service.extract_segments(SIMPLE_HTML)
        reconstructed = service.reconstruct(result, result.segments)

        validation = service.validate_integrity(
            SIMPLE_HTML, reconstructed, result.token_map
        )

        assert validation.is_valid
        assert not validation.errors

    def test_missing_protected_element_fails_validation(self, service):
        """Si se pierde un shortcode en la reconstrucción, la validación falla."""
        result = service.extract_segments(HTML_WITH_SHORTCODES)
        # Reconstrucción deliberadamente corrupta: eliminamos el shortcode
        bad_html = SIMPLE_HTML  # no contiene el shortcode original

        validation = service.validate_integrity(
            HTML_WITH_SHORTCODES, bad_html, result.token_map
        )

        assert not validation.is_valid
        assert len(validation.errors) > 0
        assert len(validation.missing_tokens) > 0

    def test_leftover_token_marker_fails_validation(self, service):
        """Si queda un token sin reemplazar en el HTML final, la validación falla."""
        corrupt_html = SIMPLE_HTML + " ⟦WP_0⟧ ⟦SEG_1⟧"
        validation = service.validate_integrity(SIMPLE_HTML, corrupt_html, {})

        assert not validation.is_valid

    def test_too_short_reconstruction_fails_validation(self, service):
        """Si el HTML resultante es mucho más corto que el original, la validación falla."""
        validation = service.validate_integrity(
            original_html=SIMPLE_HTML,
            reconstructed_html="<p>Texto muy corto.</p>",
            token_map={},
        )

        assert not validation.is_valid
        # Verificamos que hay un error de longitud — el mensaje incluye siempre el porcentaje
        assert any("%" in e for e in validation.errors), (
            f"Se esperaba un error de ratio de longitud, se obtuvo: {validation.errors}"
        )

    def test_warning_for_much_longer_reconstruction(self, service):
        """
        Un HTML mucho más largo que el original genera un warning, no un error.
        Es una señal de posible alucinación del LLM, no un fallo crítico.
        """
        bloated_html = SIMPLE_HTML * 10  # 10x el tamaño original
        validation = service.validate_integrity(SIMPLE_HTML, bloated_html, {})

        assert len(validation.warnings) > 0
        # No necesariamente error — depende del ratio configurado
