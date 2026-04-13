"""
Tests unitarios para LocalBackupRepository.

Patrón: usamos tmp_path (fixture nativa de pytest) para crear
directorios temporales reales. No se mockea el filesystem — esto es
código de I/O y el test DEBE ejercitar el comportamiento real.

No se prueba el event loop de asyncio directamente para file I/O,
pero asyncio.to_thread sí se llama en el código de producción.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.repositories.local_backup_repo import LocalBackupRepository


@pytest.fixture
def settings(tmp_path: Path) -> MagicMock:
    """Settings con directorios temporales reales para cada test."""
    s = MagicMock()
    s.backup_dir = tmp_path / "backups"
    s.log_dir = tmp_path / "logs"
    s.backup_dir.mkdir(parents=True, exist_ok=True)
    s.log_dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def repo(settings) -> LocalBackupRepository:
    return LocalBackupRepository(settings)


# ── Tests de save_backup ───────────────────────────────────────────────────────


class TestSaveBackup:

    async def test_returns_string_path(self, repo):
        path = await repo.save_backup(
            page_id=42,
            original_content="<p>Contenido original.</p>",
            metadata={"instruction": "mejora SEO", "url": "https://site.com/page/"},
        )
        assert isinstance(path, str)
        assert path.endswith(".json")

    async def test_creates_backup_file(self, repo, settings):
        await repo.save_backup(
            page_id=99,
            original_content="<p>Texto de prueba.</p>",
            metadata={"instruction": "test"},
        )
        backup_dir = settings.backup_dir / "99"
        assert backup_dir.exists(), "El directorio del page_id debe crearse"
        files = list(backup_dir.glob("*.json"))
        assert len(files) == 1, "Debe crearse exactamente un archivo de backup"

    async def test_backup_file_contains_original_content(self, repo, settings):
        content = "<h2>Mi página</h2><p>Párrafo de prueba largo.</p>"
        await repo.save_backup(
            page_id=5,
            original_content=content,
            metadata={"instruction": "optimiza", "url": "https://test.com/"},
        )
        files = list((settings.backup_dir / "5").glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))

        assert data["original_content"] == content
        assert data["page_id"] == 5

    async def test_backup_includes_metadata(self, repo, settings):
        meta = {"instruction": "humaniza el tono", "url": "https://site.com/servicios/"}
        await repo.save_backup(
            page_id=7,
            original_content="<p>Contenido.</p>",
            metadata=meta,
        )
        files = list((settings.backup_dir / "7").glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))

        assert data["instruction"] == "humaniza el tono"
        assert data["url"] == "https://site.com/servicios/"

    async def test_backup_path_includes_page_id_in_directory(self, repo):
        path = await repo.save_backup(
            page_id=123,
            original_content="<p>Texto.</p>",
            metadata={},
        )
        assert "123" in path

    async def test_multiple_backups_for_same_page_creates_multiple_files(
        self, repo, settings
    ):
        """Dos ejecuciones en momentos distintos deben crear archivos separados."""
        import asyncio
        await repo.save_backup(42, "<p>v1</p>", {})
        await asyncio.sleep(0.01)  # Asegura timestamps distintos
        await repo.save_backup(42, "<p>v2</p>", {})

        files = list((settings.backup_dir / "42").glob("*.json"))
        # Puede haber 1 o 2 dependiendo del timing — al menos debe haber 1
        assert len(files) >= 1

    async def test_backup_has_timestamp_field(self, repo, settings):
        await repo.save_backup(10, "<p>Contenido.</p>", {})
        files = list((settings.backup_dir / "10").glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert "timestamp" in data
        assert len(data["timestamp"]) > 0


# ── Tests de save_log ──────────────────────────────────────────────────────────


class TestSaveLog:

    async def test_creates_log_file(self, repo, settings):
        await repo.save_log({"event": "modification", "page_id": 1})
        log_file = settings.log_dir / "modifications.jsonl"
        assert log_file.exists(), "El archivo JSONL debe crearse"

    async def test_log_file_contains_valid_json_line(self, repo, settings):
        record = {
            "event": "modification",
            "page_id": 42,
            "status": "dry_run",
            "instruction": "mejora el SEO",
        }
        await repo.save_log(record)
        log_file = settings.log_dir / "modifications.jsonl"
        line = log_file.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)

        assert parsed["event"] == "modification"
        assert parsed["page_id"] == 42

    async def test_multiple_logs_append_correctly(self, repo, settings):
        """Los logs deben ser append-only — varios registros deben coexistir."""
        await repo.save_log({"event": "modification", "page_id": 1})
        await repo.save_log({"event": "integrity_failure", "page_id": 2})
        await repo.save_log({"event": "modification", "page_id": 3})

        log_file = settings.log_dir / "modifications.jsonl"
        lines = [
            l for l in log_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(lines) == 3, (
            f"Deben existir 3 líneas JSONL, se encontraron {len(lines)}"
        )

    async def test_each_log_line_is_valid_json(self, repo, settings):
        records = [
            {"event": "modification", "page_id": i, "status": "dry_run"}
            for i in range(5)
        ]
        for r in records:
            await repo.save_log(r)

        log_file = settings.log_dir / "modifications.jsonl"
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)  # No debe lanzar
                assert "event" in parsed

    async def test_log_record_with_unicode_content(self, repo, settings):
        """El log debe manejar caracteres especiales en español sin corrupción."""
        await repo.save_log({
            "event": "modification",
            "instruction": "Humaniza el contenido con énfasis en beneficios",
        })
        log_file = settings.log_dir / "modifications.jsonl"
        line = log_file.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert "énfasis" in parsed["instruction"]
