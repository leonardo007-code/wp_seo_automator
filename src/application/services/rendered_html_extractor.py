from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag

from src.domain.entities import BuilderType, EditableSegment

logger = logging.getLogger(__name__)

_MIN_BY_KIND = {
    "heading": 3,
    "paragraph": 20,
    "list": 8,
    "button": 2,
    "anchor": 2,
    "alt": 5,
    "fallback": 15,
}

_EDITABLE_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "li",
        "blockquote",
        "figcaption",
        "td",
        "th",
        "button",
    }
)

_DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "meta",
    "link",
    "head",
    "iframe",
    "canvas",
    "svg",
)

_SKIP_CLASS_PATTERNS_COMMON = re.compile(
    r"\b(nav|navbar|menu|breadcrumb|footer|cookie|modal|popup|"
    r"sidebar|drawer|offcanvas|wpadminbar|skip-link)\b",
    re.IGNORECASE,
)

_SKIP_CLASS_PATTERNS_BY_BUILDER: dict[BuilderType, re.Pattern[str]] = {
    BuilderType.ELEMENTOR: re.compile(
        r"\b(elementor-nav-menu|elementor-widget-nav-menu|"
        r"elementor-widget-theme-site-logo|elementor-widget-search-form)\b",
        re.IGNORECASE,
    ),
    BuilderType.OXYGEN: re.compile(
        r"\b(oxy-header|oxy-pro-menu|ct-menu|ct-header)\b",
        re.IGNORECASE,
    ),
    BuilderType.BREAKDANCE: re.compile(
        r"\b(bde-menu|bde-mobile-menu|bde-breadcrumbs)\b",
        re.IGNORECASE,
    ),
    BuilderType.BRICKS: re.compile(
        r"\b(bricks-nav-menu|bricks-mobile-menu|bricks-breadcrumbs)\b",
        re.IGNORECASE,
    ),
    BuilderType.DIVI: re.compile(
        r"\b(et_pb_menu|et_pb_fullwidth_menu|et_pb_social_media_follow)\b",
        re.IGNORECASE,
    ),
    BuilderType.CLASSIC: re.compile(r"$a"),
    BuilderType.GUTENBERG: re.compile(r"$a"),
    BuilderType.UNKNOWN: re.compile(r"$a"),
}

_HIDDEN_STYLE_PATTERN = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
_HIDDEN_CLASS_PATTERN = re.compile(r"\b(hidden|is-hidden|sr-only|screen-reader-text)\b", re.IGNORECASE)
_TAB_ACCORDION_HINT = re.compile(r"\b(tab|tabs|accordion|toggle|faq|panel|content)\b", re.IGNORECASE)
_FILENAME_LIKE_ALT = re.compile(r"\.(png|jpe?g|webp|gif|svg)$", re.IGNORECASE)
_ONLY_SYMBOLS_OR_NUMBERS = re.compile(r"^[\d\s\+\-\(\)\/\._:;,|]+$")


def _tag_attrs(tag: Tag) -> dict:
    raw = getattr(tag, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def _class_string(tag: Tag) -> str:
    cls = _tag_attrs(tag).get("class")
    if not cls:
        return ""
    if isinstance(cls, str):
        return cls
    return " ".join(str(c) for c in cls)


def _style_string(tag: Tag) -> str:
    style = _tag_attrs(tag).get("style")
    return style if isinstance(style, str) else ""


def _kind_for_element(tag_name: str) -> str:
    if tag_name.startswith("h") and len(tag_name) == 2 and tag_name[1].isdigit():
        return "heading"
    if tag_name == "p":
        return "paragraph"
    if tag_name == "li":
        return "list"
    if tag_name == "button":
        return "button"
    if tag_name == "a":
        return "anchor"
    if tag_name == "img:alt":
        return "alt"
    return "fallback"


class RenderedHTMLExtractor:
    """Builder-aware extractor for rendered HTML."""

    def __init__(self, timeout: int = 20) -> None:
        self._timeout = timeout

    async def extract_from_url(self, url: str, builder_type: BuilderType | None = None) -> list[EditableSegment]:
        try:
            html = await self._fetch_html(url)
            return self._parse_segments(html, builder_type=builder_type)
        except Exception as e:
            logger.warning(
                "RenderedHTMLExtractor failed - returning empty segments",
                extra={"url": url, "error": str(e)},
            )
            return []

    def extract_from_html(self, html: str, builder_type: BuilderType | None = None) -> list[EditableSegment]:
        return self._parse_segments(html, builder_type=builder_type)

    async def _fetch_html(self, url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; WPSEOAutomator/1.0; "
                "+https://github.com/wp-seo-automator)"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    def _parse_segments(self, html: str, builder_type: BuilderType | None = None) -> list[EditableSegment]:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        detected = builder_type or self._infer_builder(soup)
        self._clean_dom(soup, detected)

        segments: list[EditableSegment] = []
        seen_texts: set[str] = set()

        def append_segment(tag_name: str, text: str) -> None:
            normalized = re.sub(r"\s+", " ", text).strip()
            if not normalized:
                return
            if normalized in seen_texts:
                return
            kind = _kind_for_element(tag_name)
            if len(normalized) < _MIN_BY_KIND[kind]:
                return
            if _ONLY_SYMBOLS_OR_NUMBERS.fullmatch(normalized):
                return
            seen_texts.add(normalized)
            segments.append(EditableSegment(index=len(segments), tag=tag_name, text=normalized))

        for element in soup.find_all(_EDITABLE_TAGS):
            if not isinstance(element, Tag):
                continue
            if self._is_hidden(element):
                continue
            append_segment(element.name, element.get_text(separator=" ", strip=True))

        # Tabs/accordions often keep meaningful text in div wrappers.
        for container in soup.find_all("div"):
            if not isinstance(container, Tag):
                continue
            if not _TAB_ACCORDION_HINT.search(_class_string(container)):
                continue
            append_segment("div", container.get_text(separator=" ", strip=True))

        for anchor in soup.find_all("a"):
            if not isinstance(anchor, Tag):
                continue
            if self._is_hidden(anchor):
                continue
            if not self._is_relevant_anchor(anchor):
                continue
            append_segment("a", anchor.get_text(separator=" ", strip=True))

        for img in soup.find_all("img"):
            if not isinstance(img, Tag):
                continue
            alt = str(_tag_attrs(img).get("alt", "")).strip()
            if not alt:
                continue
            if _FILENAME_LIKE_ALT.search(alt) or "image" in alt.lower():
                continue
            append_segment("img:alt", alt)

        logger.info(
            "RenderedHTML extraction complete",
            extra={"segments_found": len(segments), "builder": detected.value},
        )
        return segments

    def _clean_dom(self, soup: BeautifulSoup, builder_type: BuilderType) -> None:
        for tag_name in _DROP_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()

        for tag_name in ("form", "nav", "header", "footer", "aside"):
            for el in soup.find_all(tag_name):
                el.decompose()

        builder_skip = _SKIP_CLASS_PATTERNS_BY_BUILDER.get(
            builder_type,
            _SKIP_CLASS_PATTERNS_BY_BUILDER[BuilderType.UNKNOWN],
        )
        for el in soup.find_all(True):
            if not isinstance(el, Tag):
                continue
            cls = _class_string(el)
            if _SKIP_CLASS_PATTERNS_COMMON.search(cls) or builder_skip.search(cls):
                el.decompose()

    def _is_hidden(self, element: Tag) -> bool:
        attrs = _tag_attrs(element)
        cls = _class_string(element)

        if _TAB_ACCORDION_HINT.search(cls):
            return False

        if attrs.get("hidden") is not None:
            return True
        if str(attrs.get("aria-hidden", "")).lower() == "true":
            return True
        if _HIDDEN_STYLE_PATTERN.search(_style_string(element)):
            return True
        if _HIDDEN_CLASS_PATTERN.search(cls):
            return True
        return False

    @staticmethod
    def _is_relevant_anchor(anchor: Tag) -> bool:
        attrs = _tag_attrs(anchor)
        href = str(attrs.get("href", "")).strip()
        text = anchor.get_text(separator=" ", strip=True)
        if not text:
            return False
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            return False
        return True

    @staticmethod
    def _infer_builder(soup: BeautifulSoup) -> BuilderType:
        html = str(soup)
        checks: list[tuple[BuilderType, tuple[str, ...]]] = [
            (BuilderType.ELEMENTOR, ("data-elementor-type", "elementor-section", "elementor-widget-container")),
            (BuilderType.OXYGEN, ("oxy-", "ct_section", "ct_div_block")),
            (BuilderType.BREAKDANCE, ("bde-", "data-breakdance")),
            (BuilderType.BRICKS, ("brxe-", "bricks-")),
            (BuilderType.DIVI, ("et_pb_",)),
        ]
        for builder, signatures in checks:
            if any(sig in html for sig in signatures):
                return builder
        return BuilderType.UNKNOWN
