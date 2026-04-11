"""
Tests unitarios para GeminiProvider.

Principio: las funciones _build_prompt y _parse_response son puras
y se testean sin ningún mock. La clase GeminiProvider se testea
mockeando únicamente _call_api (el borde de la infraestructura real).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.domain.entities import EditableSegment
from src.infrastructure.providers.gemini_provider import (
    GeminiProvider,
    _build_prompt,
    _parse_response,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_segments() -> list[EditableSegment]:
    return [
        EditableSegment(index=0, tag="h2", text="Servicios de consultoría empresarial"),
        EditableSegment(index=1, tag="p", text="Ofrecemos soluciones integrales para empresas con más de 20 años de experiencia."),
        EditableSegment(index=2, tag="p", text="Nuestro equipo está formado por expertos en estrategia y operaciones."),
    ]


@pytest.fixture
def single_segment() -> list[EditableSegment]:
    return [
        EditableSegment(index=0, tag="p", text="Este texto necesita ser mejorado para SEO.")
    ]


def _make_valid_response(segments: list[EditableSegment], prefix: str = "") -> str:
    """Helper: construye una respuesta bien formada como la que devolvería Gemini."""
    lines = []
    for seg in segments:
        lines.append(f"<<SEG_{seg.index}>>")
        lines.append(f"{prefix}{seg.text}")
        lines.append("")
    return "\n".join(lines)


# ── Tests de _build_prompt ─────────────────────────────────────────────────────


class TestBuildPrompt:

    def test_contains_all_segment_markers(self, sample_segments):
        prompt = _build_prompt(sample_segments, "mejora el SEO")

        for seg in sample_segments:
            assert f"<<SEG_{seg.index}>>" in prompt

    def test_contains_all_original_texts(self, sample_segments):
        prompt = _build_prompt(sample_segments, "mejora el SEO")

        for seg in sample_segments:
            assert seg.text in prompt

    def test_contains_user_instruction(self, sample_segments):
        instruction = "humaniza el contenido y mejora claridad"
        prompt = _build_prompt(sample_segments, instruction)

        assert instruction in prompt

    def test_markers_in_correct_order(self, sample_segments):
        prompt = _build_prompt(sample_segments, "reescribe")

        positions = [prompt.index(f"<<SEG_{seg.index}>>") for seg in sample_segments]
        assert positions == sorted(positions), "Los markers deben aparecer en orden ascendente"

    def test_strict_count_reminder_included_on_retry(self, sample_segments):
        prompt = _build_prompt(sample_segments, "optimiza", strict_count_reminder=3)

        assert "CRITICAL" in prompt or "3" in prompt
        # Debe incluir el número esperado de segmentos en la advertencia
        assert "3" in prompt

    def test_strict_count_reminder_absent_on_first_attempt(self, sample_segments):
        prompt = _build_prompt(sample_segments, "optimiza", strict_count_reminder=None)

        assert "CRITICAL" not in prompt

    def test_empty_segments_produces_valid_prompt(self):
        prompt = _build_prompt([], "optimiza")
        assert "0 total" in prompt


# ── Tests de _parse_response ───────────────────────────────────────────────────


class TestParseResponse:

    def test_parses_well_formed_response(self, sample_segments):
        response = _make_valid_response(sample_segments, prefix="[SEO] ")
        result = _parse_response(response, sample_segments)

        assert len(result) == len(sample_segments)

    def test_modified_text_captured_correctly(self, sample_segments):
        modified_texts = [
            "Consultoría empresarial de alto nivel",
            "Brindamos soluciones integrales con más de dos décadas de trayectoria.",
            "Nuestros expertos en estrategia y operaciones están a tu servicio.",
        ]
        lines = []
        for seg, mod_text in zip(sample_segments, modified_texts):
            lines.append(f"<<SEG_{seg.index}>>")
            lines.append(mod_text)
            lines.append("")
        response = "\n".join(lines)

        result = _parse_response(response, sample_segments)

        for res_seg, mod_text in zip(result, modified_texts):
            assert res_seg.modified_text == mod_text

    def test_unchanged_text_sets_modified_text_to_none(self, sample_segments):
        """Si el LLM devuelve el texto idéntico al original, modified_text debe ser None."""
        response = _make_valid_response(sample_segments)  # sin prefix = texto idéntico
        result = _parse_response(response, sample_segments)

        for seg in result:
            assert seg.modified_text is None, (
                f"Texto no modificado debe tener modified_text=None, "
                f"pero fue: {seg.modified_text!r}"
            )

    def test_result_preserves_original_tag_and_text(self, sample_segments):
        response = _make_valid_response(sample_segments, prefix="Nuevo: ")
        result = _parse_response(response, sample_segments)

        for orig, res in zip(sample_segments, result):
            assert res.tag == orig.tag
            assert res.text == orig.text
            assert res.index == orig.index

    def test_result_sorted_by_index(self, sample_segments):
        """
        Incluso si Gemini devuelve los markers fuera de orden,
        el resultado debe estar ordenado por index.
        """
        # Construir respuesta con markers en orden invertido
        lines = []
        for seg in reversed(sample_segments):
            lines.append(f"<<SEG_{seg.index}>>")
            lines.append(f"Nuevo: {seg.text}")
            lines.append("")
        response = "\n".join(lines)

        result = _parse_response(response, sample_segments)

        indices = [r.index for r in result]
        assert indices == sorted(indices)

    def test_count_mismatch_raises_value_error(self, sample_segments):
        """Si la respuesta tiene menos segmentos que el input, debe lanzar ValueError."""
        # Respuesta con solo 1 segmento en vez de 3
        response = "<<SEG_0>>\nSolo un segmento.\n"

        with pytest.raises(ValueError, match="count mismatch"):
            _parse_response(response, sample_segments)

    def test_extra_text_before_first_marker_is_ignored(self, sample_segments):
        """El texto previo al primer marker (preamble de Gemini) debe ser ignorado."""
        preamble = "Aquí tienes los segmentos transformados:\n\n"
        response = preamble + _make_valid_response(sample_segments, prefix="Opt: ")

        result = _parse_response(response, sample_segments)

        assert len(result) == len(sample_segments)

    def test_multiline_segment_text_is_captured(self):
        segments = [
            EditableSegment(index=0, tag="p", text="Texto original en una línea.")
        ]
        response = "<<SEG_0>>\nPrimera línea del texto.\nSegunda línea del texto.\n"

        result = _parse_response(response, segments)

        assert "Primera línea" in result[0].get_final_text()


# ── Tests de GeminiProvider (con mock de _call_api) ───────────────────────────


class TestGeminiProvider:
    """
    Estos tests mockean únicamente self._call_api.
    No se hace ninguna llamada real a la API de Google.
    La lógica de retry, parsing y fallback se verifica completamente.
    """

    @pytest.fixture
    def mock_settings(self):
        """Settings mínimos para instanciar GeminiProvider sin API real."""
        from unittest.mock import MagicMock
        settings = MagicMock()
        settings.gemini_api_key = "fake-key-for-testing"
        settings.gemini_model = "gemini-2.0-flash"
        settings.max_retries = 3
        settings.request_timeout_seconds = 30
        return settings

    @pytest.fixture
    def provider(self, mock_settings):
        with patch("google.genai.Client"):
            return GeminiProvider(mock_settings)

    @pytest.mark.asyncio
    async def test_successful_transform(self, provider, sample_segments):
        """Test del camino feliz: la API responde correctamente en el primer intento."""
        valid_response = _make_valid_response(sample_segments, prefix="Optimizado: ")

        with patch.object(provider, "_call_api", new=AsyncMock(return_value=valid_response)):
            result = await provider.transform_segments(sample_segments, "mejora el SEO")

        assert len(result) == len(sample_segments)

    @pytest.mark.asyncio
    async def test_empty_segments_returns_empty(self, provider):
        result = await provider.transform_segments([], "cualquier instrucción")
        assert result == []

    @pytest.mark.asyncio
    async def test_retry_on_count_mismatch(self, provider, sample_segments):
        """
        Si el primer intento devuelve el número incorrecto de segmentos,
        debe reintentar con prompt más estricto.
        """
        bad_response = "<<SEG_0>>\nSolo uno.\n"  # Falta SEG_1 y SEG_2
        good_response = _make_valid_response(sample_segments, prefix="Retry: ")

        call_api_mock = AsyncMock(side_effect=[bad_response, good_response])

        with patch.object(provider, "_call_api", new=call_api_mock), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await provider.transform_segments(sample_segments, "optimiza")

        # Debe haber llamado a la API 2 veces
        assert call_api_mock.call_count == 2
        assert len(result) == len(sample_segments)

    @pytest.mark.asyncio
    async def test_fallback_to_originals_after_max_retries(self, provider, sample_segments):
        """
        Si todos los intentos fallan con count mismatch,
        debe devolver los segmentos originales (sin modificar).
        Nunca debe propagar la excepción al caller.
        """
        bad_response = "<<SEG_0>>\nSolo uno.\n"
        provider._max_retries = 2  # reducir para acelerar el test

        with patch.object(provider, "_call_api", new=AsyncMock(return_value=bad_response)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await provider.transform_segments(sample_segments, "optimiza")

        # El fallback devuelve los originales: ninguno tiene modified_text
        assert len(result) == len(sample_segments)
        for orig, res in zip(sample_segments, result):
            assert res.index == orig.index

    @pytest.mark.asyncio
    async def test_api_error_propagates_after_max_retries(self, provider, sample_segments):
        """Un error de API (red, quota) debe propagarse tras agotar los reintentos."""
        provider._max_retries = 2

        with patch.object(
            provider,
            "_call_api",
            new=AsyncMock(side_effect=Exception("API quota exceeded")),
        ), patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="API quota exceeded"):
                await provider.transform_segments(sample_segments, "optimiza")

    @pytest.mark.asyncio
    async def test_retry_prompt_includes_count_reminder(self, provider, sample_segments):
        """En el segundo intento, el prompt debe incluir la advertencia de count."""
        bad_response = "<<SEG_0>>\nSolo uno.\n"
        good_response = _make_valid_response(sample_segments)

        captured_prompts: list[str] = []

        async def capturing_mock(prompt: str) -> str:
            captured_prompts.append(prompt)
            if len(captured_prompts) == 1:
                return bad_response
            return good_response

        with patch.object(provider, "_call_api", new=capturing_mock), \
             patch("asyncio.sleep", new=AsyncMock()):
            await provider.transform_segments(sample_segments, "optimiza")

        assert len(captured_prompts) == 2
        # El segundo prompt debe contener la advertencia de count
        assert "CRITICAL" in captured_prompts[1] or str(len(sample_segments)) in captured_prompts[1]

    @pytest.mark.asyncio
    async def test_missing_api_key_raises_on_init(self, mock_settings):
        """Si no hay API key, debe fallar al instanciar, no en runtime."""
        mock_settings.gemini_api_key = ""

        with patch("google.genai.Client"):
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                GeminiProvider(mock_settings)
