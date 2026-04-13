"""
Tests unitarios para DiffService.

Filosofía: testear contratos observables — qué devuelve cada método
bajo cada condición. No se mockea difflib ni nada interno.
"""
from __future__ import annotations

import pytest

from src.application.services.diff_service import DiffService
from src.domain.entities import EditableSegment


@pytest.fixture
def service() -> DiffService:
    return DiffService()


@pytest.fixture
def original_segments() -> list[EditableSegment]:
    return [
        EditableSegment(index=0, tag="h2", text="Servicios de consultoría empresarial"),
        EditableSegment(index=1, tag="p", text="Ofrecemos soluciones integrales con 20 años de experiencia."),
        EditableSegment(index=2, tag="p", text="Nuestro equipo son expertos en estrategia y operaciones."),
    ]


@pytest.fixture
def modified_segments() -> list[EditableSegment]:
    return [
        EditableSegment(
            index=0, tag="h2",
            text="Servicios de consultoría empresarial",
            modified_text="Consultoría empresarial de alto nivel",
        ),
        EditableSegment(
            index=1, tag="p",
            text="Ofrecemos soluciones integrales con 20 años de experiencia.",
            modified_text="Brindamos soluciones integrales con más de dos décadas de trayectoria.",
        ),
        EditableSegment(
            index=2, tag="p",
            text="Nuestro equipo son expertos en estrategia y operaciones.",
            # Este segmento no cambia — modified_text=None
        ),
    ]


# ── Tests de generate_diff ─────────────────────────────────────────────────────


class TestGenerateDiff:

    def test_identical_content_returns_no_differences(self, service):
        result = service.generate_diff("mismo texto", "mismo texto")
        assert result == "(no differences)"

    def test_different_content_returns_diff_string(self, service):
        original = "Texto original de la página."
        modified = "Texto modificado de la página."
        result = service.generate_diff(original, modified)
        assert result != "(no differences)"

    def test_diff_contains_minus_for_removed_lines(self, service):
        result = service.generate_diff("línea eliminada\n", "línea nueva\n")
        assert "-línea eliminada" in result

    def test_diff_contains_plus_for_added_lines(self, service):
        result = service.generate_diff("línea original\n", "línea nueva\n")
        assert "+línea nueva" in result

    def test_empty_strings_returns_no_differences(self, service):
        result = service.generate_diff("", "")
        assert result == "(no differences)"

    def test_multiline_html_diff(self, service):
        original = "<h2>Título</h2>\n<p>Párrafo original.</p>\n"
        modified = "<h2>Título Mejorado</h2>\n<p>Párrafo optimizado para SEO.</p>\n"
        result = service.generate_diff(original, modified)
        assert "original" in result or "---" in result

    def test_returns_string_type(self, service):
        result = service.generate_diff("a", "b")
        assert isinstance(result, str)


# ── Tests de generate_segments_diff ───────────────────────────────────────────


class TestGenerateSegmentsDiff:

    def test_returns_no_changes_when_all_identical(self, service, original_segments):
        # Segmentos sin modified_text — todos idénticos al original
        result = service.generate_segments_diff(original_segments, original_segments)
        assert result == "(no segment changes detected)"

    def test_detects_changed_segments(self, service, original_segments, modified_segments):
        result = service.generate_segments_diff(original_segments, modified_segments)
        assert result != "(no segment changes detected)"

    def test_shows_original_text_in_diff(self, service, original_segments, modified_segments):
        result = service.generate_segments_diff(original_segments, modified_segments)
        assert "Ofrecemos soluciones integrales" in result or "ORIGINAL" in result

    def test_shows_modified_text_in_diff(self, service, original_segments, modified_segments):
        result = service.generate_segments_diff(original_segments, modified_segments)
        assert "Brindamos" in result or "MODIFIED" in result

    def test_count_header_reflects_changes(self, service, original_segments, modified_segments):
        result = service.generate_segments_diff(original_segments, modified_segments)
        # 2 of 3 segments changed
        assert "2" in result

    def test_unchanged_segments_not_in_diff(self, service, original_segments, modified_segments):
        """El segmento #2 no cambió — su texto original no debe aparecer como MODIFIED."""
        result = service.generate_segments_diff(original_segments, modified_segments)
        # El tercer segmento no debería mostrar "MODIFIED : Nuestro equipo..."
        # porque get_final_text() devuelve el texto original si modified_text es None
        lines = result.splitlines()
        modified_lines = [l for l in lines if "MODIFIED" in l]
        # Solo 2 segmentos cambiaron
        assert len(modified_lines) == 2

    def test_empty_lists_returns_no_changes(self, service):
        result = service.generate_segments_diff([], [])
        assert result == "(no segment changes detected)"

    def test_returns_string_type(self, service, original_segments, modified_segments):
        result = service.generate_segments_diff(original_segments, modified_segments)
        assert isinstance(result, str)

    def test_tag_and_index_in_diff_header(self, service, original_segments, modified_segments):
        """Cada segmento modificado debe mostrar su tag e índice."""
        result = service.generate_segments_diff(original_segments, modified_segments)
        # Espera algo como "── [H2 #0] ──" o "[P #1]"
        assert "#0" in result or "H2" in result


# ── Tests de compute_change_ratio ─────────────────────────────────────────────


class TestComputeChangeRatio:

    def test_all_unchanged_ratio_is_zero(self, service, original_segments):
        ratio = service.compute_change_ratio(original_segments, original_segments)
        assert ratio == 0.0

    def test_all_changed_ratio_is_one(self, service, original_segments):
        all_changed = [
            EditableSegment(
                index=s.index,
                tag=s.tag,
                text=s.text,
                modified_text=f"Versión nueva: {s.text}",
            )
            for s in original_segments
        ]
        ratio = service.compute_change_ratio(original_segments, all_changed)
        assert ratio == pytest.approx(1.0)

    def test_partial_change_ratio(self, service, original_segments, modified_segments):
        # 2 de 3 segmentos cambiaron
        ratio = service.compute_change_ratio(original_segments, modified_segments)
        assert ratio == pytest.approx(2 / 3)

    def test_empty_input_returns_zero(self, service):
        ratio = service.compute_change_ratio([], [])
        assert ratio == 0.0

    def test_ratio_returns_float(self, service, original_segments, modified_segments):
        ratio = service.compute_change_ratio(original_segments, modified_segments)
        assert isinstance(ratio, float)
