"""
builder_detector.py — Detección del builder de WordPress usado en una página.

Responsabilidad única: examinar raw_content (shortcodes/HTML) y retornar
cuál builder fue detectado, con qué confianza y qué implicaciones tiene
para la extracción y publicación.

No extrae texto. No transforma. Solo detecta.

Diseño de las señales:
  Ordenadas por especificidad descendente. Más específico = mayor confianza.
  Si múltiples builders tienen señales, gana el que tiene más hits pesados.
  UNKNOWN se retorna cuando no hay señal definitiva o están en conflicto.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.domain.entities import (
    BuilderType,
    ExtractionMode,
    ExtractionReport,
    PolicyDecision,
)

# ── Política de publicación por builder ───────────────────────────────────────
# Esta tabla es el contrato de seguridad del sistema.
# NO cambiar sin entender las implicaciones de cada builder.
_POLICY_TABLE: dict[BuilderType, tuple[PolicyDecision, str]] = {
    BuilderType.GUTENBERG: (
        PolicyDecision.ALLOW,
        "",
    ),
    BuilderType.CLASSIC: (
        PolicyDecision.ALLOW,
        "",
    ),
    BuilderType.DIVI: (
        PolicyDecision.ALLOW_WITH_CAUTION,
        "",
    ),
    BuilderType.ELEMENTOR: (
        PolicyDecision.ANALYSIS_ONLY,
        "Elementor stores page data in post meta (_elementor_data), not in "
        "content.raw. Publishing via REST API content field would corrupt the "
        "Elementor layout. Use dry_run=true for analysis only.",
    ),
    BuilderType.OXYGEN: (
        PolicyDecision.ANALYSIS_ONLY,
        "Oxygen Builder stores its data in post meta (ct_builder_shortcodes). "
        "Direct content.raw modification is not supported.",
    ),
    BuilderType.BREAKDANCE: (
        PolicyDecision.ANALYSIS_ONLY,
        "Breakdance stores its page data in post meta. "
        "Direct content.raw modification is not supported.",
    ),
    BuilderType.BRICKS: (
        PolicyDecision.ANALYSIS_ONLY,
        "Bricks Builder stores its data in post meta (bricks_data). "
        "Direct content.raw modification is not supported.",
    ),
    BuilderType.UNKNOWN: (
        PolicyDecision.ANALYSIS_ONLY,
        "Builder could not be detected with confidence. Publishing is blocked "
        "to prevent accidental corruption. Use dry_run=true.",
    ),
}

# ── Modo de extracción por builder ────────────────────────────────────────────
_EXTRACTION_MODE_TABLE: dict[BuilderType, ExtractionMode] = {
    BuilderType.GUTENBERG:  ExtractionMode.STANDARD,
    BuilderType.CLASSIC:    ExtractionMode.STANDARD,
    BuilderType.DIVI:       ExtractionMode.DIVI_SHORTCODE,
    BuilderType.ELEMENTOR:  ExtractionMode.RENDERED_HTML,
    BuilderType.OXYGEN:     ExtractionMode.RENDERED_HTML,
    BuilderType.BREAKDANCE: ExtractionMode.RENDERED_HTML,
    BuilderType.BRICKS:     ExtractionMode.RENDERED_HTML,
    BuilderType.UNKNOWN:    ExtractionMode.NONE,
}


@dataclass
class _Signal:
    """Una señal de detección individual."""
    pattern: re.Pattern[str]
    builder: BuilderType
    weight: float          # 0.0 - 1.0 — cuánto contribuye a la confianza
    description: str       # Para logging y el ExtractionReport


# ── Señales de detección ordenadas por especificidad ─────────────────────────
# Cuanto más específica, mayor peso. El orden importa para el logging.
_SIGNALS: list[_Signal] = [
    # ── Divi ─────────────────────────────────────────────────────────────────
    _Signal(
        re.compile(r"\[et_pb_section\b", re.IGNORECASE),
        BuilderType.DIVI,
        0.90,
        "Divi et_pb_section shortcode",
    ),
    _Signal(
        re.compile(r"\[et_pb_row\b", re.IGNORECASE),
        BuilderType.DIVI,
        0.80,
        "Divi et_pb_row shortcode",
    ),
    _Signal(
        re.compile(r"\[et_pb_", re.IGNORECASE),
        BuilderType.DIVI,
        0.70,
        "Divi et_pb_ shortcode prefix",
    ),
    _Signal(
        re.compile(r"_builder_version\s*=\s*\"4\.\d+", re.IGNORECASE),
        BuilderType.DIVI,
        0.60,
        "Divi _builder_version attribute",
    ),
    # ── Elementor ────────────────────────────────────────────────────────────
    _Signal(
        re.compile(r'data-elementor-type\s*=', re.IGNORECASE),
        BuilderType.ELEMENTOR,
        0.95,
        "Elementor data-elementor-type attribute in rendered HTML",
    ),
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*elementor-section[^"\']*["\']', re.IGNORECASE),
        BuilderType.ELEMENTOR,
        0.90,
        "Elementor elementor-section CSS class",
    ),
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*elementor-widget-container[^"\']*["\']', re.IGNORECASE),
        BuilderType.ELEMENTOR,
        0.85,
        "Elementor widget container class",
    ),
    _Signal(
        re.compile(r'<!--\s*elementor\b', re.IGNORECASE),
        BuilderType.ELEMENTOR,
        0.85,
        "Elementor HTML comment marker",
    ),
    _Signal(
        re.compile(r'"elType"\s*:', re.IGNORECASE),
        BuilderType.ELEMENTOR,
        0.75,
        "Elementor JSON elType key in meta",
    ),
    # ── Oxygen ───────────────────────────────────────────────────────────────
    _Signal(
        re.compile(r'\[ct_section\b', re.IGNORECASE),
        BuilderType.OXYGEN,
        0.90,
        "Oxygen ct_section shortcode",
    ),
    _Signal(
        re.compile(r'\[ct_div_block\b', re.IGNORECASE),
        BuilderType.OXYGEN,
        0.85,
        "Oxygen ct_div_block shortcode",
    ),
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*oxy-[a-z]', re.IGNORECASE),
        BuilderType.OXYGEN,
        0.80,
        "Oxygen oxy- CSS class prefix",
    ),
    _Signal(
        re.compile(r'id\s*=\s*["\']ct-ultimate-google-font', re.IGNORECASE),
        BuilderType.OXYGEN,
        0.70,
        "Oxygen Google Fonts meta ID",
    ),
    # ── Breakdance ───────────────────────────────────────────────────────────
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*bde-[a-z]', re.IGNORECASE),
        BuilderType.BREAKDANCE,
        0.90,
        "Breakdance bde- CSS class prefix",
    ),
    _Signal(
        re.compile(r'data-breakdance', re.IGNORECASE),
        BuilderType.BREAKDANCE,
        0.90,
        "Breakdance data attribute",
    ),
    _Signal(
        re.compile(r'"breakdanceElements"\s*:', re.IGNORECASE),
        BuilderType.BREAKDANCE,
        0.85,
        "Breakdance JSON elements key",
    ),
    # ── Bricks ───────────────────────────────────────────────────────────────
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*brxe-[a-z]', re.IGNORECASE),
        BuilderType.BRICKS,
        0.90,
        "Bricks brxe- CSS element class",
    ),
    _Signal(
        re.compile(r'class\s*=\s*["\'][^"\']*bricks-', re.IGNORECASE),
        BuilderType.BRICKS,
        0.85,
        "Bricks bricks- CSS class prefix",
    ),
    _Signal(
        re.compile(r'<!--\s*\[bricks\b', re.IGNORECASE),
        BuilderType.BRICKS,
        0.85,
        "Bricks comment marker",
    ),
    # ── Gutenberg ────────────────────────────────────────────────────────────
    _Signal(
        re.compile(r'<!--\s*wp:[a-zA-Z]', re.IGNORECASE),
        BuilderType.GUTENBERG,
        0.95,
        "Gutenberg wp: block comment",
    ),
    # ── Classic Editor ───────────────────────────────────────────────────────
    # Classic es el fallback cuando hay HTML puro y ningún builder fue detectado.
    # Se detecta por la AUSENCIA de builders y la PRESENCIA de HTML convencional.
    _Signal(
        re.compile(r'<p\b[^>]*>[^<]{10,}</p>', re.IGNORECASE),
        BuilderType.CLASSIC,
        0.30,   # Peso bajo — solo suma si no hay nada más
        "Plain HTML paragraph (classic editor indicator)",
    ),
]

# Umbral mínimo de confianza para declarar un builder con certeza
_CONFIDENCE_THRESHOLD = 0.55


class BuilderDetector:
    """
    Detecta el builder de WordPress usado en una página.

    Evalúa señales ordenadas por peso. El builder con mayor score acumulado
    gana. UNKNOWN se retorna si ningún builder supera el umbral de confianza
    o si hay empate entre builders incompatibles.

    Uso:
        detector = BuilderDetector()
        report = detector.detect(raw_content)
        print(report.builder_type, report.confidence, report.publish_allowed)
    """

    def detect(self, raw_content: str, rendered_content: str = "") -> ExtractionReport:
        """
        Analiza raw_content y rendered_content y retorna un ExtractionReport completo.

        Args:
            raw_content: El contenido crudo obtenido de la REST API de WP.
                         Puede ser shortcodes (Divi, Oxygen...) o HTML.
            rendered_content: El HTML renderizado devuelto por the_content via REST API.

        Returns:
            ExtractionReport con builder_type, confidence, policy_decision,
            extraction_mode, publish_blocked_reason y detection_signals.
        """
        # Acumulador de scores por builder
        scores: dict[BuilderType, float] = {b: 0.0 for b in BuilderType}
        signals_fired: list[str] = []

        combined_content = f"{raw_content}\n\n{rendered_content}"

        for signal in _SIGNALS:
            if signal.pattern.search(combined_content):
                scores[signal.builder] += signal.weight
                signals_fired.append(signal.description)

        # Determinar ganador
        # Classic tiene peso muy bajo, solo gana si NADA más fue detectado
        best_builder, best_score = max(
            ((b, s) for b, s in scores.items() if b != BuilderType.CLASSIC),
            key=lambda x: x[1],
            default=(BuilderType.UNKNOWN, 0.0),
        )

        # Si ningún builder de terceros ganó, verificar si al menos hay HTML clásico
        if best_score < _CONFIDENCE_THRESHOLD:
            if scores[BuilderType.CLASSIC] > 0:
                best_builder = BuilderType.CLASSIC
                best_score = min(scores[BuilderType.CLASSIC], 0.70)
            else:
                best_builder = BuilderType.UNKNOWN
                best_score = 0.0

        # Normalizar confianza a 0.0-1.0
        confidence = min(best_score, 1.0)

        # Obtener política y modo de extracción
        policy_decision, blocked_reason = _POLICY_TABLE[best_builder]
        extraction_mode = _EXTRACTION_MODE_TABLE[best_builder]

        return ExtractionReport(
            builder_type=best_builder,
            extraction_mode=extraction_mode,
            confidence=round(confidence, 3),
            policy_decision=policy_decision,
            publish_blocked_reason=blocked_reason,
            detection_signals=signals_fired,
        )
