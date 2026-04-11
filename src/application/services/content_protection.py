from __future__ import annotations

import logging
import re
from typing import Final

from bs4 import BeautifulSoup

from src.domain.entities import EditableSegment, ProtectedContent, ValidationResult

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# Minimum character count for a text node to be considered "editable".
# Filters out navigation labels, lone punctuation, accessibility strings, etc.
MIN_TEXT_LENGTH: Final[int] = 15

# Tags whose direct text content is considered editable.
# We deliberately exclude <span> (often structural/inline) and <div> (never pure text).
# <td>/<th> included because WP table blocks generate these with prose content.
EDITABLE_TAGS: Final[frozenset[str]] = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "figcaption", "td", "th",
})

# Unicode brackets (U+27E6/U+27E7) — chosen because they never appear in real WP HTML.
# This eliminates false positives in token detection.
_WP_TOK_OPEN: Final[str] = "⟦WP_"
_WP_TOK_CLOSE: Final[str] = "⟧"
_SEG_TOK_OPEN: Final[str] = "⟦SEG_"
_SEG_TOK_CLOSE: Final[str] = "⟧"

# Safety bounds for reconstructed HTML length relative to original.
_MIN_LENGTH_RATIO: Final[float] = 0.40
_MAX_LENGTH_RATIO: Final[float] = 4.00

# ── Protection Patterns ────────────────────────────────────────────────────────
# ORDER IS CRITICAL. More specific / longer-match patterns must precede shorter ones.
# A <script> inside a <form> must be tokenized as SCRIPT before FORM swallows it.
#
# Each tuple: (compiled_pattern, human_label_for_logging)

_PROTECTION_PATTERNS: Final[list[tuple[re.Pattern[str], str]]] = [
    # 1. <script> — always first; JS can contain anything that would confuse later patterns.
    (
        re.compile(r"<script\b[^>]*?>.*?</script>", re.DOTALL | re.IGNORECASE),
        "SCRIPT",
    ),
    # 2. <style> — inline CSS blocks.
    (
        re.compile(r"<style\b[^>]*?>.*?</style>", re.DOTALL | re.IGNORECASE),
        "STYLE",
    ),
    # 3. <iframe> — embeds, maps, videos. Never touch these.
    (
        re.compile(r"<iframe\b[^>]*?>.*?</iframe>", re.DOTALL | re.IGNORECASE),
        "IFRAME",
    ),
    # 4. <form> — contact forms, WooCommerce checkout, etc.
    (
        re.compile(r"<form\b[^>]*?>.*?</form>", re.DOTALL | re.IGNORECASE),
        "FORM",
    ),
    # 5. <noscript> — fallback content; structural, not editorial.
    (
        re.compile(r"<noscript\b[^>]*?>.*?</noscript>", re.DOTALL | re.IGNORECASE),
        "NOSCRIPT",
    ),
    # 6. Gutenberg block comment markers.
    #    We protect the MARKERS (<!-- wp:* --> and <!-- /wp:* -->), NOT their inner HTML.
    #    The inner HTML (the actual <p>, <h2>, etc.) is what we want to edit.
    #    Both self-closing (<!-- wp:spacer /--> ) and paired markers are matched here.
    (
        re.compile(r"<!--\s*/?wp:[a-zA-Z][^>]*?-->", re.DOTALL),
        "WP_BLOCK_MARKER",
    ),
    # 7. WP shortcodes with wrapped content: [gallery id="1"] ... [/gallery]
    #    Must come before simple shortcodes to avoid partial matches.
    (
        re.compile(
            r"\[[a-zA-Z][a-zA-Z0-9_-]*(?:\s[^\]]*?)?\].*?\[/[a-zA-Z][a-zA-Z0-9_-]*\]",
            re.DOTALL,
        ),
        "WP_SHORTCODE_BLOCK",
    ),
    # 8. WP simple / self-closing shortcodes: [contact-form-7 id="1"] or [gallery /]
    (
        re.compile(r"\[[a-zA-Z][a-zA-Z0-9_-]*(?:\s[^\]]*?)?/?]"),
        "WP_SHORTCODE_SIMPLE",
    ),
]

# ── Service ────────────────────────────────────────────────────────────────────


class ContentProtectionService:
    """
    Implements IContentProtectionService.

    This service is the structural guardian of the system.
    It guarantees that WordPress layout, blocks, shortcodes, scripts,
    iframes and forms are never exposed to the LLM.

    Three-phase workflow
    ────────────────────
    Phase A — Tokenization:
        Replace protected elements with ⟦WP_N⟧ tokens.
        Nothing after this step can accidentally modify WP internals.

    Phase B — Segment Extraction:
        Parse the now-safe tokenized HTML with BeautifulSoup.
        Locate pure-text editable nodes. Replace their text with ⟦SEG_N⟧.
        Export the resulting 'segmented_html' back as a string.

    Reconstruction:
        Replace ⟦SEG_N⟧ with LLM output (or original if unchanged).
        Replace ⟦WP_N⟧ with their stored originals — byte for byte.

    Validation:
        Confirm all WP values are present in the output.
        Confirm length ratios are sane.
        Confirm no placeholder token leaked through.
    """

    # ── Public API (implements IContentProtectionService) ─────────────────────

    def extract_segments(self, raw_html: str) -> ProtectedContent:
        """
        Full extraction pipeline.
        Returns a ProtectedContent object ready for LLM consumption.
        """
        token_map: dict[str, str] = {}

        # Phase A: protect WP-specific elements
        tokenized_html = self._tokenize_protected_elements(raw_html, token_map)

        # Phase B: extract editable segments and inject SEG placeholders
        segments, segmented_html = self._extract_editable_segments(tokenized_html)

        logger.info(
            "Segment extraction complete",
            extra={
                "protected_tokens": len(token_map),
                "editable_segments": len(segments),
            },
        )

        return ProtectedContent(
            raw_html=raw_html,
            token_map=token_map,
            segments=segments,
            # tokenized_html stores the html with BOTH ⟦WP_N⟧ and ⟦SEG_N⟧ tokens
            tokenized_html=segmented_html,
        )

    def reconstruct(
        self,
        protected: ProtectedContent,
        new_segments: list[EditableSegment],
    ) -> str:
        """
        Rebuilds the final HTML from the segmented template.

        Step 1: Replace ⟦SEG_N⟧ with LLM-provided (or original) text.
        Step 2: Restore ⟦WP_N⟧ with their stored originals — exact bytes.

        Both steps work at string level, NOT through BS4. This is intentional:
        BS4 is only used for tree navigation during extraction, never for output.
        """
        html = protected.tokenized_html

        # Step 1 — inject segment texts
        segment_by_index = {seg.index: seg for seg in new_segments}
        for idx, segment in segment_by_index.items():
            tok = f"{_SEG_TOK_OPEN}{idx}{_SEG_TOK_CLOSE}"
            clean_text = self._sanitize_llm_text(segment.get_final_text())
            html = html.replace(tok, clean_text, 1)

        # Safety check: did any SEG token remain unreplaced?
        orphaned_seg = re.findall(r"⟦SEG_\d+⟧", html)
        if orphaned_seg:
            logger.warning(
                "Orphaned SEG tokens after reconstruction — using originals",
                extra={"tokens": orphaned_seg},
            )
            # Fallback: replace orphans with original text from segments list
            original_by_index = {seg.index: seg for seg in protected.segments}
            for tok in orphaned_seg:
                idx_str = tok[len(_SEG_TOK_OPEN) : -len(_SEG_TOK_CLOSE)]
                idx = int(idx_str)
                if idx in original_by_index:
                    html = html.replace(tok, original_by_index[idx].text, 1)

        # Step 2 — restore WP tokens to their exact original values
        for token, original_value in protected.token_map.items():
            html = html.replace(token, original_value, 1)

        return html

    def validate_integrity(
        self,
        original_html: str,
        reconstructed_html: str,
        token_map: dict[str, str],
    ) -> ValidationResult:
        """
        Structural sanity validation.

        IMPORTANT: This does NOT check byte-for-byte identity.
        That would be impossible and is NOT the goal.
        We validate FUNCTIONAL preservation:
          - All protected WP elements are present in output.
          - No placeholder token leaked into the result.
          - Length ratio is within expected bounds.
        """
        errors: list[str] = []
        warnings: list[str] = []
        missing_tokens: list[str] = []

        # Check 1: All protected original values are present in reconstructed output.
        for token, original_value in token_map.items():
            if original_value not in reconstructed_html:
                sig = original_value[:80].replace("\n", " ")
                missing_tokens.append(sig)
                errors.append(f"Protected element missing from reconstruction: {sig!r}")

        # Check 2: No raw token markers leaked into the final output.
        leaked = re.findall(r"⟦(?:WP|SEG)_[^⟧]*⟧", reconstructed_html)
        if leaked:
            errors.append(f"Unreplaced token markers in final HTML: {leaked}")

        # Check 3: Length sanity.
        if len(original_html) > 0:
            ratio = len(reconstructed_html) / len(original_html)
            if ratio < _MIN_LENGTH_RATIO:
                errors.append(
                    f"Reconstructed HTML is {ratio:.0%} of original length. "
                    "Possible content loss — refusing to publish."
                )
            elif ratio > _MAX_LENGTH_RATIO:
                warnings.append(
                    f"Reconstructed HTML is {ratio:.0%} of original. "
                    "LLM may have injected unexpected content."
                )

        return ValidationResult(
            is_valid=len(errors) == 0,
            warnings=warnings,
            errors=errors,
            missing_tokens=missing_tokens,
        )

    # ── Private: Phase A — Tokenization ───────────────────────────────────────

    def _tokenize_protected_elements(
        self, html: str, token_map: dict[str, str]
    ) -> str:
        """
        Iterates protection patterns in order and replaces each match with a
        unique ⟦WP_N⟧ token stored in token_map.

        Uses a mutable list [counter] to avoid nonlocal complexity inside lambdas.
        """
        _counter: list[int] = [0]

        def _make_token(original_value: str) -> str:
            tok = f"{_WP_TOK_OPEN}{_counter[0]}{_WP_TOK_CLOSE}"
            token_map[tok] = original_value
            _counter[0] += 1
            return tok

        for pattern, label in _PROTECTION_PATTERNS:
            prev = html
            html = pattern.sub(lambda m: _make_token(m.group(0)), html)
            replaced = _counter[0]
            if html != prev:
                logger.debug(
                    "Tokenized protected elements",
                    extra={"type": label, "total_tokens_so_far": replaced},
                )

        return html

    # ── Private: Phase B — Segment Extraction ─────────────────────────────────

    def _extract_editable_segments(
        self, tokenized_html: str
    ) -> tuple[list[EditableSegment], str]:
        """
        Parses the tokenized HTML (safe — no WP elements remain).
        Finds editable tags that contain ONLY a direct text node (no child HTML tags).
        Replaces their text with ⟦SEG_N⟧ placeholders in the tree.
        Exports the modified soup back as string (the 'segmented_html').

        Why only tags with element.string != None?
        ────────────────────────────────────────────
        element.string returns a value only when the element has a single child
        that is a NavigableString (i.e., pure text, no inner HTML tags).
        If the element has mixed content like <p>Text <strong>bold</strong></p>,
        element.string is None and we skip it — we will not mutilate inline tags.
        This is conservative but safe for MVP.
        """
        soup = BeautifulSoup(tokenized_html, "html.parser")
        segments: list[EditableSegment] = []
        index = 0
        skipped_complex = 0

        for element in soup.find_all(EDITABLE_TAGS):
            # Only pure-text nodes (no child tags)
            if element.string is None:
                skipped_complex += 1
                continue

            text = element.string.strip()

            # Skip trivially short content
            if len(text) < MIN_TEXT_LENGTH:
                continue

            # Skip if content is just a WP token placeholder (already protected above)
            if re.fullmatch(r"⟦WP_\d+⟧", text):
                continue

            seg_token = f"{_SEG_TOK_OPEN}{index}{_SEG_TOK_CLOSE}"
            segments.append(EditableSegment(index=index, tag=element.name, text=text))

            # Modify the soup tree: replace text node with SEG placeholder
            element.string.replace_with(seg_token)
            index += 1

        if skipped_complex:
            logger.info(
                "Skipped elements with mixed content (inline child tags)",
                extra={"count": skipped_complex},
            )

        # Export: html.parser does not add DOCTYPE or <html>/<body> wrappers for fragments.
        segmented_html = str(soup)
        return segments, segmented_html

    # ── Private: Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_llm_text(text: str) -> str:
        """
        Strip any accidental token markers from LLM output.
        A misbehaving LLM should not be able to inject our placeholder syntax.
        """
        sanitized = text.replace("⟦", "").replace("⟧", "")
        if sanitized != text:
            logger.warning("LLM output contained token marker characters — stripped.")
        return sanitized
