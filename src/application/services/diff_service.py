from __future__ import annotations

import difflib
import logging

from src.domain.entities import EditableSegment

logger = logging.getLogger(__name__)


class DiffService:
    """
    Implements IDiffService.
    Generates human-readable diffs between original and modified content.

    Two levels of comparison:
    1. Segment-level: shows each changed segment individually (for the API response).
    2. Full HTML: unified diff of the full HTML (for audit logs and preview).
    """

    def generate_diff(self, original: str, modified: str) -> str:
        """
        Generates a unified diff between two multi-line strings.
        Suitable for logs and full-document comparison.
        Returns '(no differences)' when content is identical.
        """
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile="original",
            tofile="modified",
            lineterm="\n",
        )
        result = "".join(diff)
        return result if result.strip() else "(no differences)"

    def generate_segments_diff(
        self,
        original_segments: list[EditableSegment],
        modified_segments: list[EditableSegment],
    ) -> str:
        """
        Generates a segment-by-segment human-readable comparison.
        Only includes segments that actually changed.
        Used in the API response for quick review before publishing.
        """
        if len(original_segments) != len(modified_segments):
            logger.warning(
                "Segment count mismatch in diff generation",
                extra={
                    "original_count": len(original_segments),
                    "modified_count": len(modified_segments),
                },
            )

        lines: list[str] = []
        changed_count = 0

        for orig, mod in zip(original_segments, modified_segments):
            orig_text = orig.text
            mod_text = mod.get_final_text()

            if orig_text == mod_text:
                continue

            changed_count += 1
            lines.append(f"── [{orig.tag.upper()} #{orig.index}] ──")
            lines.append(f"  ORIGINAL : {orig_text}")
            lines.append(f"  MODIFIED : {mod_text}")
            lines.append("")

        if not lines:
            return "(no segment changes detected)"

        header = f"Changed segments: {changed_count} of {len(original_segments)}\n\n"
        return header + "\n".join(lines)

    def compute_change_ratio(
        self,
        original_segments: list[EditableSegment],
        modified_segments: list[EditableSegment],
    ) -> float:
        """
        Returns the fraction of segments that were actually modified.
        Useful for detecting LLM hallucinations (ratio = 1.0 when nothing should have changed)
        or no-op responses (ratio = 0.0 when changes were expected).
        """
        if not original_segments:
            return 0.0

        changed = sum(
            1
            for orig, mod in zip(original_segments, modified_segments)
            if orig.text != mod.get_final_text()
        )
        return changed / len(original_segments)
