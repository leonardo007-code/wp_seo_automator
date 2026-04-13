"""Tests for builder-aware rendered HTML extraction."""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.application.services.rendered_html_extractor import (
    RenderedHTMLExtractor,
    _class_string,
    _style_string,
)
from src.domain.entities import BuilderType
from tests.fixtures.page_content_fixtures import (
    BREAKDANCE_RENDERED_HTML,
    BRICKS_RENDERED_HTML,
    ELEMENTOR_RENDERED_HTML,
    OXYGEN_RENDERED_HTML,
)


def test_class_and_style_helpers_when_attrs_is_none() -> None:
    soup = BeautifulSoup("<div class=\"skip-me\"></div>", "html.parser")
    tag = soup.div
    assert tag is not None
    tag.attrs = None  # type: ignore[assignment]
    assert _class_string(tag) == ""
    assert _style_string(tag) == ""


def test_extract_elementor_headings_paragraphs_lists() -> None:
    segments = RenderedHTMLExtractor().extract_from_html(
        ELEMENTOR_RENDERED_HTML,
        builder_type=BuilderType.ELEMENTOR,
    )
    texts = [s.text for s in segments]
    assert any("Diagn" in t for t in texts)
    assert any("Utilizamos" in t for t in texts)
    assert any("Electro" in t for t in texts)


def test_extract_oxygen_heading_and_paragraph() -> None:
    segments = RenderedHTMLExtractor().extract_from_html(
        OXYGEN_RENDERED_HTML,
        builder_type=BuilderType.OXYGEN,
    )
    tags = [s.tag for s in segments]
    assert "h1" in tags
    assert "p" in tags


def test_extract_breakdance_and_bricks_content() -> None:
    extractor = RenderedHTMLExtractor()
    breakdance_segments = extractor.extract_from_html(
        BREAKDANCE_RENDERED_HTML,
        builder_type=BuilderType.BREAKDANCE,
    )
    bricks_segments = extractor.extract_from_html(
        BRICKS_RENDERED_HTML,
        builder_type=BuilderType.BRICKS,
    )
    assert len(breakdance_segments) >= 2
    assert len(bricks_segments) >= 2


def test_extract_buttons_anchors_tabs_and_accordions() -> None:
    html = """
    <div class="elementor-section">
      <button>Book consultation</button>
      <a href="#cta">Start your project</a>
      <div class="elementor-tab-title">Travel Planning</div>
      <div class="elementor-tab-content" style="display:none">Custom Peru itineraries for agencies and operators.</div>
      <div class="elementor-accordion-item">
        <div class="elementor-accordion-title">Frequently Asked Questions</div>
        <div class="elementor-accordion-content">We answer logistics, timing, and service quality questions.</div>
      </div>
    </div>
    """
    segments = RenderedHTMLExtractor().extract_from_html(html, builder_type=BuilderType.ELEMENTOR)
    texts = [s.text for s in segments]
    assert any("Book consultation" in t for t in texts)
    assert any("Start your project" in t for t in texts)
    assert any("Custom Peru itineraries" in t for t in texts)
    assert any("Frequently Asked Questions" in t for t in texts)


def test_extract_alt_text_and_relevant_anchor_only() -> None:
    html = """
    <div>
      <img src="hero.webp" alt="Luxury Peru travel itinerary consultation" />
      <img src="logo.png" alt="logo.png" />
      <a href="javascript:void(0)">Menu trigger</a>
      <a href="/contact">Talk to our local DMC team</a>
    </div>
    """
    segments = RenderedHTMLExtractor().extract_from_html(html, builder_type=BuilderType.UNKNOWN)
    tags = [s.tag for s in segments]
    texts = [s.text for s in segments]
    assert "img:alt" in tags
    assert any("Luxury Peru travel itinerary" in t for t in texts)
    assert any("Talk to our local DMC team" in t for t in texts)
    assert not any("Menu trigger" in t for t in texts)


def test_ignores_forms_iframes_and_navigation_noise() -> None:
    html = """
    <nav><a href="/x">Main menu item should not appear</a></nav>
    <form><label>Hidden business form label</label><input type="text" /></form>
    <iframe src="https://example.com"></iframe>
    <div class="elementor-widget-container">
      <p>This paragraph should survive widget container filtering.</p>
    </div>
    """
    segments = RenderedHTMLExtractor().extract_from_html(html, builder_type=BuilderType.ELEMENTOR)
    texts = [s.text for s in segments]
    assert any("should survive" in t for t in texts)
    assert not any("Main menu item" in t for t in texts)
    assert not any("form label" in t for t in texts)


def test_returns_empty_when_no_useful_text() -> None:
    html = "<div><img src='x.png' alt='' /><span>OK</span></div>"
    segments = RenderedHTMLExtractor().extract_from_html(html, builder_type=BuilderType.UNKNOWN)
    assert segments == []


def test_output_is_stable_and_indexed() -> None:
    html = "<h2>Peru DMC Services</h2><p>We provide tailored travel operations with local experts.</p>"
    segments = RenderedHTMLExtractor().extract_from_html(html, builder_type=BuilderType.UNKNOWN)
    assert [s.index for s in segments] == list(range(len(segments)))
    assert [s.tag for s in segments] == ["h2", "p"]
