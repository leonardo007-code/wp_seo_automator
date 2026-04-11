"""
Tests de la API Layer (Fase 6).

Patrón: TestClient con dependency_overrides en get_modify_page_use_case.
El lifespan se ejecuta pero solo crea WpRestClient y BackupRepo con settings falsos.
El use case es completamente mockeado — ninguna llamada real a WP o Gemini.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_modify_page_use_case
from src.domain.entities import ModificationResult, ModificationStatus
from src.domain.exceptions import (
    ContentIntegrityError,
    WordPressAuthError,
    WordPressPageNotFoundError,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_result(**overrides) -> ModificationResult:
    defaults = dict(
        page_id=42,
        page_url="https://site.com/servicios/",
        instruction="mejora el SEO",
        status=ModificationStatus.DRY_RUN,
        dry_run=True,
        segments_found=3,
        segments_modified=2,
        diff_summary="Changed segments: 2 of 3",
        backup_path="/backups/42/20240101T120000Z.json",
        original_content="<p>Texto original.</p>",
        proposed_content="<p>Texto optimizado para SEO.</p>",
        warnings=[],
        errors=[],
        created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ModificationResult(**defaults)


_VALID_BODY = {
    "identifier": "servicios",
    "instructions": "Mejora el SEO de este contenido.",
    "dry_run": True,
}


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_use_case():
    uc = AsyncMock()
    uc.execute.return_value = _make_result()
    return uc


@pytest.fixture
def client(mock_use_case):
    """
    TestClient con el use case mockeado.
    El lifespan crea WpRestClient y BackupRepo con fake settings del conftest.
    Se parchea google.genai.Client para evitar validaciones de API key en el import.
    """
    from src.main import app

    app.dependency_overrides[get_modify_page_use_case] = lambda: mock_use_case

    with patch("google.genai.Client"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    app.dependency_overrides.clear()


# ── Tests: Health Check ────────────────────────────────────────────────────────


class TestHealthCheck:

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_contains_status_ok(self, client):
        data = client.get("/health").json()
        assert data["status"] == "ok"

    def test_health_contains_llm_backend(self, client):
        data = client.get("/health").json()
        assert "llm_backend" in data
        assert data["llm_backend"] == "gemini"


# ── Tests: POST /api/v1/modifications ─────────────────────────────────────────


class TestPostModifications:

    def test_valid_dry_run_request_returns_200(self, client):
        response = client.post("/api/v1/modifications", json=_VALID_BODY)
        assert response.status_code == 200

    def test_response_schema_has_required_fields(self, client):
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()

        required_fields = [
            "page_id", "page_url", "instruction", "status",
            "dry_run", "segments_found", "segments_modified",
            "diff_summary", "backup_path", "original_content",
            "proposed_content", "warnings", "errors", "created_at",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_dry_run_status_in_response(self, client):
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()
        assert data["status"] == "dry_run"
        assert data["dry_run"] is True

    def test_use_case_called_with_correct_args(self, client, mock_use_case):
        client.post("/api/v1/modifications", json=_VALID_BODY)

        mock_use_case.execute.assert_called_once_with(
            identifier="servicios",
            instructions="Mejora el SEO de este contenido.",
            dry_run=True,
        )

    def test_apply_mode_sets_dry_run_false(self, client, mock_use_case):
        mock_use_case.execute.return_value = _make_result(
            status=ModificationStatus.SUCCESS, dry_run=False
        )
        body = {**_VALID_BODY, "dry_run": False}
        data = client.post("/api/v1/modifications", json=body).json()

        assert data["status"] == "success"
        assert data["dry_run"] is False

    def test_response_contains_diff_summary(self, client):
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()
        assert data["diff_summary"] == "Changed segments: 2 of 3"

    def test_response_contains_backup_path(self, client):
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()
        assert "/backups/42/" in data["backup_path"]

    def test_response_contains_original_and_proposed_content(self, client):
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()
        assert data["original_content"] == "<p>Texto original.</p>"
        assert data["proposed_content"] == "<p>Texto optimizado para SEO.</p>"

    def test_warnings_in_response(self, client, mock_use_case):
        mock_use_case.execute.return_value = _make_result(
            warnings=["Reconstructed HTML is 300% of original."]
        )
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()
        assert len(data["warnings"]) == 1
        assert "300%" in data["warnings"][0]


# ── Tests: Request Validation ──────────────────────────────────────────────────


class TestRequestValidation:

    def test_empty_identifier_returns_422(self, client):
        body = {**_VALID_BODY, "identifier": ""}
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_missing_identifier_returns_422(self, client):
        body = {"instructions": "mejora SEO", "dry_run": True}
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_missing_instructions_returns_422(self, client):
        body = {"identifier": "servicios", "dry_run": True}
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_too_short_instructions_returns_422(self, client):
        body = {**_VALID_BODY, "instructions": "SEO"}  # less than min_length=5
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_dry_run_defaults_to_true_when_omitted(self, client, mock_use_case):
        body = {"identifier": "servicios", "instructions": "Mejora el contenido SEO."}
        client.post("/api/v1/modifications", json=body)

        call_kwargs = mock_use_case.execute.call_args[1]
        assert call_kwargs["dry_run"] is True


# ── Tests: Exception Handling ──────────────────────────────────────────────────


class TestExceptionHandling:

    def test_page_not_found_returns_404(self, client, mock_use_case):
        mock_use_case.execute.side_effect = WordPressPageNotFoundError(
            "No page found for slug 'inexistente'"
        )
        response = client.post("/api/v1/modifications", json=_VALID_BODY)

        assert response.status_code == 404
        data = response.json()
        assert data["error_type"] == "page_not_found"
        assert "inexistente" in data["detail"]

    def test_auth_error_returns_401(self, client, mock_use_case):
        mock_use_case.execute.side_effect = WordPressAuthError(
            "Authentication failed. Check credentials."
        )
        response = client.post("/api/v1/modifications", json=_VALID_BODY)

        assert response.status_code == 401
        assert response.json()["error_type"] == "authentication_error"

    def test_integrity_error_returns_422(self, client, mock_use_case):
        mock_use_case.execute.side_effect = ContentIntegrityError(
            "Structural integrity check failed."
        )
        response = client.post("/api/v1/modifications", json=_VALID_BODY)

        assert response.status_code == 422
        assert response.json()["error_type"] == "integrity_validation_failed"

    def test_llm_provider_error_returns_503(self, client, mock_use_case):
        from src.domain.exceptions import LLMProviderError
        mock_use_case.execute.side_effect = LLMProviderError("Quota exceeded")
        response = client.post("/api/v1/modifications", json=_VALID_BODY)

        assert response.status_code == 503
        assert response.json()["error_type"] == "llm_provider_error"

    def test_not_implemented_backend_returns_501(self, client, mock_use_case):
        mock_use_case.execute.side_effect = NotImplementedError("Backend not implemented")
        response = client.post("/api/v1/modifications", json=_VALID_BODY)

        assert response.status_code == 501
        assert response.json()["error_type"] == "not_implemented"

    def test_error_response_has_detail_and_error_type(self, client, mock_use_case):
        mock_use_case.execute.side_effect = WordPressPageNotFoundError("Not found")
        data = client.post("/api/v1/modifications", json=_VALID_BODY).json()

        assert "detail" in data
        assert "error_type" in data


# ── Tests: Validaciones adicionales (bugs detectados en auditoría) ─────────────


class TestAdditionalValidation:

    def test_whitespace_only_instructions_returns_422(self, client):
        """'     ' (5 espacios) debe rechazarse — whitespace-only no es instrucción válida."""
        body = {**_VALID_BODY, "instructions": "     "}  # 5 espacios — pasa min_length sin validator
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_whitespace_only_identifier_returns_422(self, client):
        body = {**_VALID_BODY, "identifier": "   "}  # 3 espacios
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 422

    def test_valid_numeric_identifier_accepted(self, client):
        """ID numérico puro como string debe aceptarse."""
        body = {**_VALID_BODY, "identifier": "42"}
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 200

    def test_valid_url_identifier_accepted(self, client):
        """URL completa debe aceptarse como identifier."""
        body = {**_VALID_BODY, "identifier": "https://site.com/servicios/"}
        response = client.post("/api/v1/modifications", json=body)
        assert response.status_code == 200
