from __future__ import annotations

import asyncio
import logging
import re
from typing import Final

from google import genai
from google.genai import types as genai_types

from src.config.settings import Settings
from src.domain.entities import EditableSegment

logger = logging.getLogger(__name__)

# ── Segment Marker Protocol ────────────────────────────────────────────────────
# We use <<SEG_N>> as delimiters in the prompt and parse them from the response.
# Chosen because:
#   - Unlikely to appear in editorial prose.
#   - Simple enough for a regex split (no lookahead required).
#   - Survives Gemini's occasional whitespace normalization.

_MARKER_PREFIX: Final[str] = "<<SEG_"
_MARKER_SUFFIX: Final[str] = ">>"
_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(r"<<SEG_(\d+)>>\s*")

# ── System Instruction ─────────────────────────────────────────────────────────
# This is the immutable contract sent to Gemini on every call.
# It lives here, not in the use case — prompt engineering is a provider concern.

_SYSTEM_INSTRUCTION: Final[str] = """
You are a professional SEO content editor and copywriter.
You will receive a list of plain text segments from a WordPress page.
Each segment is prefixed with a marker like <<SEG_0>>, <<SEG_1>>, etc.

STRICT OUTPUT RULES:
1. Return EXACTLY the same number of segments as provided — no more, no less.
2. Keep each marker exactly as written (<<SEG_N>>) on its own line.
3. Write the transformed text on the line(s) immediately after its marker.
4. Do NOT merge, split, skip, or reorder segments.
5. Do NOT add markdown (no asterisks, no #, no bullet points).
6. Do NOT add HTML tags of any kind.
7. Do NOT invent facts, figures, statistics, or information not in the original.
8. Do NOT change the language of the text.
9. Preserve the original commercial intent and meaning unless explicitly instructed.
10. If a segment cannot be meaningfully improved, return it word-for-word unchanged.
""".strip()

_STRICT_COUNT_REMINDER: Final[str] = """
CRITICAL: The previous attempt returned the wrong number of segments.
You MUST return EXACTLY {expected} segments, one per marker. No exceptions.
""".strip()


# ── Module-level pure functions (testable without instantiating the class) ─────


def _build_prompt(
    segments: list[EditableSegment],
    instructions: str,
    strict_count_reminder: int | None = None,
) -> str:
    """
    Builds the full user prompt for the LLM.

    Structure:
    1. User instructions (free text).
    2. Optional strict count reminder (used on retries).
    3. Segments, each preceded by its <<SEG_N>> marker.

    Args:
        segments: List of segments to transform.
        instructions: The user's content editing instruction.
        strict_count_reminder: If not None, includes a count warning with this number.
    """
    lines: list[str] = []

    lines.append("=== INSTRUCTIONS ===")
    lines.append(instructions.strip())

    if strict_count_reminder is not None:
        lines.append("")
        lines.append(
            _STRICT_COUNT_REMINDER.format(expected=strict_count_reminder)
        )

    lines.append("")
    lines.append(f"=== CONTENT SEGMENTS ({len(segments)} total) ===")
    lines.append("Transform each segment according to the instructions above.")
    lines.append("")

    for seg in segments:
        lines.append(f"{_MARKER_PREFIX}{seg.index}{_MARKER_SUFFIX}")
        lines.append(seg.text)
        lines.append("")

    return "\n".join(lines)


def _parse_response(
    response_text: str,
    original_segments: list[EditableSegment],
) -> list[EditableSegment]:
    """
    Parses the LLM response back into a list of EditableSegment.

    Strategy: split the response by marker pattern.
    The pattern splits the text into: [pre-text, idx_0, text_0, idx_1, text_1, ...]

    Returns:
        List of EditableSegment with modified_text filled in where text differs.
        Segments whose modified text equals the original are returned with modified_text=None.

    Raises:
        ValueError: if the parsed count does not match original_segments count.
    """
    original_by_index = {seg.index: seg for seg in original_segments}

    # Split response by markers: produces [pre_text, '0', text_0, '1', text_1, ...]
    parts = _MARKER_PATTERN.split(response_text)

    # parts[0] is any text before the first marker — discard it
    # then we consume pairs: (index_str, content_text)
    result: list[EditableSegment] = []
    i = 1  # start after the pre-marker text
    while i < len(parts) - 1:
        raw_index = parts[i].strip()
        raw_text = parts[i + 1].strip()
        i += 2

        try:
            idx = int(raw_index)
        except ValueError:
            logger.warning("Non-integer segment index in LLM response", index=raw_index)
            continue

        if idx not in original_by_index:
            logger.warning("LLM returned unknown segment index", index=idx)
            continue

        orig = original_by_index[idx]
        modified_text = raw_text if raw_text and raw_text != orig.text else None

        result.append(
            EditableSegment(
                index=idx,
                tag=orig.tag,
                text=orig.text,
                modified_text=modified_text,
            )
        )

    if len(result) != len(original_segments):
        raise ValueError(
            f"Segment count mismatch: expected {len(original_segments)}, "
            f"parsed {len(result)} from LLM response."
        )

    # Ensure output is ordered by index to match input order
    result.sort(key=lambda s: s.index)
    return result


# ── Provider Class ─────────────────────────────────────────────────────────────


class GeminiProvider:
    """
    Implements ILLMProvider using the Google Gemini API.

    Swappability contract:
        No code outside this file (and its test) should import or reference
        GeminiProvider directly. All consumers depend on ILLMProvider.
        Switching to Ollama = create OllamaProvider, update DI factory. Done.

    Resilience strategy:
        1. First attempt with normal prompt.
        2. On segment count mismatch: retry with explicit count reminder.
        3. On final failure: return original segments unchanged (safe fallback).
        4. On API errors: exponential backoff, then propagate.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY must be set when LLM_BACKEND=gemini. "
                "Check your .env file."
            )
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model_name = settings.gemini_model
        self._max_retries = settings.max_retries
        self._timeout = settings.request_timeout_seconds

    async def transform_segments(
        self,
        segments: list[EditableSegment],
        instructions: str,
    ) -> list[EditableSegment]:
        """
        Transforms a list of text segments using Gemini.

        Guarantees:
         - Returns a list of the same length as input.
         - Never raises on partial failure — falls back to originals.
         - Logs every retry and failure with full context.
        """
        if not segments:
            return []

        prompt = _build_prompt(segments, instructions)

        for attempt in range(self._max_retries):
            try:
                logger.info(
                    "Calling Gemini API",
                    extra={
                        "attempt": attempt + 1,
                        "segments": len(segments),
                        "model": self._model_name,
                    },
                )
                response_text = await self._call_api(prompt)
                transformed = _parse_response(response_text, segments)

                logger.info(
                    "Gemini response parsed successfully",
                    extra={"transformed_count": len(transformed)},
                )
                return transformed

            except ValueError as parse_error:
                # Count mismatch — retry with stricter prompt
                logger.warning(
                    "Segment count mismatch from Gemini",
                    extra={
                        "error": str(parse_error),
                        "attempt": attempt + 1,
                    },
                )
                if attempt < self._max_retries - 1:
                    backoff = 2**attempt
                    logger.info(f"Retrying in {backoff}s with strict count reminder")
                    await asyncio.sleep(backoff)
                    prompt = _build_prompt(
                        segments,
                        instructions,
                        strict_count_reminder=len(segments),
                    )
                else:
                    logger.error(
                        "Max retries exceeded on segment count mismatch. "
                        "Returning original segments to preserve content integrity."
                    )
                    return segments

            except Exception as api_error:
                # Network error, quota exceeded, invalid key, etc.
                logger.error(
                    "Gemini API error",
                    extra={"error": str(api_error), "attempt": attempt + 1},
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    raise

        # Should not reach here, but satisfies the type checker
        return segments

    async def _call_api(self, prompt: str) -> str:
        """
        Async wrapper around the Gemini SDK call.
        Isolated here so tests can mock it cleanly without mocking the SDK internals.
        Uses google.genai (the new, non-deprecated SDK).
        """
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                temperature=0.4,        # Low: factual, consistent output
                max_output_tokens=8192,
            ),
        )
        return response.text
