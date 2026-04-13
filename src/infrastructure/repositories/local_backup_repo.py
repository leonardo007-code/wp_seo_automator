from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class LocalBackupRepository:
    """
    Implements IBackupRepository using the local filesystem.

    Storage layout:
    ─────────────────────────────────────────
    backups/
        {page_id}/
            {timestamp}.json    ← backup del HTML original antes de cada cambio

    logs/
        modifications.jsonl     ← un registro JSON por línea (append-only)

    JSONL format for logs:
        Chosen because it's append-safe (no risk of corrupting the file on crash),
        grep-friendly, and readable without a parser.

    File I/O wrapped in asyncio.to_thread:
        FastAPI runs on an async event loop. Blocking file writes would freeze
        the server. to_thread offloads them correctly to a thread pool.
    """

    def __init__(self, settings: Settings) -> None:
        self._backup_dir = settings.backup_dir
        self._log_file = settings.log_dir / "modifications.jsonl"

    async def save_backup(
        self,
        page_id: int,
        original_content: str,
        metadata: dict,
    ) -> str:
        """
        Persists the original HTML content BEFORE any modification.
        Returns the absolute path to the created backup file.

        Called at the start of every ModifyPageUseCase.execute(),
        even in dry_run mode — so we always have a record of what existed.
        """
        page_dir = self._backup_dir / str(page_id)
        page_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_file = page_dir / f"{timestamp}.json"

        payload = {
            "page_id": page_id,
            "timestamp": timestamp,
            "original_content": original_content,
            **metadata,
        }

        await asyncio.to_thread(
            self._write_json,
            backup_file,
            payload,
        )

        logger.info(
            "Backup saved",
            extra={"page_id": page_id, "path": str(backup_file)},
        )
        return str(backup_file)

    async def save_log(self, record: dict) -> None:
        """
        Appends an audit record to the JSONL log file.
        Thread-safe because each write is a single atomic append operation.
        """
        log_line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        await asyncio.to_thread(self._append_line, self._log_file, log_line)
        logger.debug("Log record saved", extra={"event": record.get("event", "modification")})

    # ── Private sync helpers (run in thread pool) ──────────────────────────────

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _append_line(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
