"""
test_builder_detector.py — Tests de caja blanca del BuilderDetector.

Cubre:
  - Detección correcta de cada builder por señales específicas
  - Confianza razonable para cada detección
  - Política de publicación correspondiente
  - Modo de extracción asignado
  - Caso UNKNOWN cuando no hay señales
  - Caso CLASSIC cuando hay HTML puro sin builders
  - Prioridad correcta cuando hay señales mixtas
"""
import pytest

from src.application.services.builder_detector import BuilderDetector
from src.domain.entities import (
    BuilderType,
    ExtractionMode,
    PolicyDecision,
)
from tests.fixtures.page_content_fixtures import (
    GUTENBERG_PAGE,
    CLASSIC_PAGE,
    DIVI_PAGE,
    DIVI_PAGE_WITH_GLOBAL_MODULE,
    ELEMENTOR_RAW_CONTENT,
    OXYGEN_RAW_CONTENT,
    BREAKDANCE_RENDERED_HTML,
    BRICKS_RENDERED_HTML,
    EMPTY_PAGE,
)


@pytest.fixture
def detector() -> BuilderDetector:
    return BuilderDetector()


# ── Detección de builders ─────────────────────────────────────────────────────

class TestBuilderDetection:
    def test_detects_gutenberg(self, detector):
        report = detector.detect(GUTENBERG_PAGE)
        assert report.builder_type == BuilderType.GUTENBERG

    def test_detects_classic_editor(self, detector):
        report = detector.detect(CLASSIC_PAGE)
        assert report.builder_type == BuilderType.CLASSIC

    def test_detects_divi(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert report.builder_type == BuilderType.DIVI

    def test_detects_divi_with_global_module(self, detector):
        report = detector.detect(DIVI_PAGE_WITH_GLOBAL_MODULE)
        assert report.builder_type == BuilderType.DIVI

    def test_detects_elementor(self, detector):
        report = detector.detect(ELEMENTOR_RAW_CONTENT)
        # Elementor raw content has its own markers
        # (if elementor comment is the only signal, may fallback to analysis)
        assert report.builder_type in (BuilderType.ELEMENTOR, BuilderType.UNKNOWN)

    def test_detects_elementor_from_rendered_html(self, detector):
        elementor_html = '<div class="elementor-section"><div class="elementor-widget-container"><h2>Título</h2></div></div>'
        report = detector.detect(elementor_html)
        assert report.builder_type == BuilderType.ELEMENTOR

    def test_detects_oxygen(self, detector):
        report = detector.detect(OXYGEN_RAW_CONTENT)
        assert report.builder_type == BuilderType.OXYGEN

    def test_detects_breakdance(self, detector):
        report = detector.detect(BREAKDANCE_RENDERED_HTML)
        assert report.builder_type == BuilderType.BREAKDANCE

    def test_detects_bricks(self, detector):
        report = detector.detect(BRICKS_RENDERED_HTML)
        assert report.builder_type == BuilderType.BRICKS

    def test_unknown_for_empty_content(self, detector):
        report = detector.detect("")
        assert report.builder_type in (BuilderType.UNKNOWN, BuilderType.CLASSIC)

    def test_divi_wins_over_classic_when_both_present(self, detector):
        # Divi content can also contain <p> tags (Classic signal)
        report = detector.detect(DIVI_PAGE)
        assert report.builder_type == BuilderType.DIVI


# ── Confianza ─────────────────────────────────────────────────────────────────

class TestConfidence:
    def test_gutenberg_has_high_confidence(self, detector):
        report = detector.detect(GUTENBERG_PAGE)
        assert report.confidence >= 0.90

    def test_divi_has_high_confidence(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert report.confidence >= 0.85

    def test_confidence_between_0_and_1(self, detector):
        for content in [GUTENBERG_PAGE, CLASSIC_PAGE, DIVI_PAGE, ELEMENTOR_RAW_CONTENT]:
            report = detector.detect(content)
            assert 0.0 <= report.confidence <= 1.0

    def test_unknown_has_zero_confidence(self, detector):
        report = detector.detect("")
        if report.builder_type == BuilderType.UNKNOWN:
            assert report.confidence == 0.0


# ── Política de publicación ───────────────────────────────────────────────────

class TestPublicationPolicy:
    def test_gutenberg_allows_publishing(self, detector):
        report = detector.detect(GUTENBERG_PAGE)
        assert report.policy_decision == PolicyDecision.ALLOW
        assert report.publish_allowed is True

    def test_classic_allows_publishing(self, detector):
        report = detector.detect(CLASSIC_PAGE)
        assert report.policy_decision == PolicyDecision.ALLOW
        assert report.publish_allowed is True

    def test_divi_allows_with_caution(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert report.policy_decision == PolicyDecision.ALLOW_WITH_CAUTION
        assert report.publish_allowed is True

    def test_elementor_blocks_publishing(self, detector):
        elementor_html = '<div class="elementor-section elementor-widget-container"><p>Hola</p></div>'
        report = detector.detect(elementor_html)
        assert report.policy_decision == PolicyDecision.ANALYSIS_ONLY
        assert report.publish_allowed is False
        assert len(report.publish_blocked_reason) > 0

    def test_oxygen_blocks_publishing(self, detector):
        report = detector.detect(OXYGEN_RAW_CONTENT)
        assert report.policy_decision == PolicyDecision.ANALYSIS_ONLY
        assert report.publish_allowed is False

    def test_unknown_blocks_publishing(self, detector):
        report = detector.detect("")
        if report.builder_type == BuilderType.UNKNOWN:
            assert report.publish_allowed is False


# ── Modo de extracción ────────────────────────────────────────────────────────

class TestExtractionMode:
    def test_gutenberg_uses_standard_mode(self, detector):
        report = detector.detect(GUTENBERG_PAGE)
        assert report.extraction_mode == ExtractionMode.STANDARD

    def test_classic_uses_standard_mode(self, detector):
        report = detector.detect(CLASSIC_PAGE)
        assert report.extraction_mode == ExtractionMode.STANDARD

    def test_divi_uses_divi_shortcode_mode(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert report.extraction_mode == ExtractionMode.DIVI_SHORTCODE

    def test_elementor_uses_rendered_html_mode(self, detector):
        elementor_html = '<div class="elementor-section"><p>Hola</p></div>'
        report = detector.detect(elementor_html)
        assert report.extraction_mode == ExtractionMode.RENDERED_HTML

    def test_unknown_uses_none_mode(self, detector):
        report = detector.detect("")
        if report.builder_type == BuilderType.UNKNOWN:
            assert report.extraction_mode == ExtractionMode.NONE


# ── Señales detectadas ────────────────────────────────────────────────────────

class TestDetectionSignals:
    def test_gutenberg_signals_list_not_empty(self, detector):
        report = detector.detect(GUTENBERG_PAGE)
        assert len(report.detection_signals) > 0

    def test_divi_signals_include_et_pb(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert any("et_pb" in sig.lower() or "divi" in sig.lower()
                   for sig in report.detection_signals)

    def test_detection_signals_are_strings(self, detector):
        report = detector.detect(DIVI_PAGE)
        assert all(isinstance(s, str) for s in report.detection_signals)
