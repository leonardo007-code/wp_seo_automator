"""
Tests unitarios para WpRestClient.

Patrón: inyectamos un AsyncMock(spec=httpx.AsyncClient) en el constructor.
No se hace ninguna llamada HTTP real. No se requiere respx ni pytest-httpx.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.domain.exceptions import (
    WordPressAPIError,
    WordPressAuthError,
    WordPressPageNotFoundError,
)
from src.infrastructure.wordpress.wp_rest_client import WpRestClient


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_response(status_code: int, data: dict | list | None = None) -> MagicMock:
    """
    Crea un mock de httpx.Response con status_code y json() configurados.
    Usamos MagicMock con spec para que falle si accedemos a atributos incorrectos.
    """
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = data if data is not None else {}
    response.text = json.dumps(data) if data else ""
    return response


def _make_page_data(
    page_id: int = 42,
    slug: str = "mi-pagina",
    title: str = "Mi Página",
    raw_content: str = "<p>Contenido de prueba.</p>",
    content_type: str = "page",
) -> dict:
    """WP REST API response simulada para una página."""
    return {
        "id": page_id,
        "slug": slug,
        "title": {"rendered": title},
        "content": {"raw": raw_content, "rendered": f"<p>{raw_content}</p>"},
        "link": f"https://test.com/{slug}/",
        "modified": "2024-01-15T10:30:00",
        "type": content_type,
    }


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.wp_base_url = "https://test.com"
    settings.wp_api_user = "admin"
    settings.wp_api_app_password = "abcd efgh ijkl mnop"
    settings.request_timeout_seconds = 30
    return settings


@pytest.fixture
def mock_http() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def client(mock_settings, mock_http) -> WpRestClient:
    return WpRestClient(settings=mock_settings, http_client=mock_http)


# ── Tests de resolve_page_id ───────────────────────────────────────────────────

class TestResolvePageId:

    async def test_numeric_string_returns_int_without_api_call(self, client, mock_http):
        """Un ID numérico debe resolverse directo, sin llamar a la API."""
        result = await client.resolve_page_id("42")
        assert result == 42
        mock_http.get.assert_not_called()

    async def test_slug_resolves_to_id(self, client, mock_http):
        """Un slug debe buscarse en el endpoint /pages."""
        mock_http.get.return_value = _make_response(200, [_make_page_data(page_id=99)])
        result = await client.resolve_page_id("mi-pagina")
        assert result == 99

    async def test_url_extracts_slug_and_resolves(self, client, mock_http):
        """Una URL pública debe extraer el slug del último segmento del path."""
        mock_http.get.return_value = _make_response(200, [_make_page_data(page_id=55)])
        result = await client.resolve_page_id("https://test.com/servicios/mi-pagina/")
        assert result == 55
        # Verificar que se buscó con el slug correcto
        call_params = mock_http.get.call_args[1]["params"]
        assert call_params["slug"] == "mi-pagina"

    async def test_slug_not_in_pages_tries_posts(self, client, mock_http):
        """Si el slug no está en /pages, debe intentar con /posts."""
        empty_pages = _make_response(200, [])
        post_response = _make_response(200, [_make_page_data(page_id=77)])
        mock_http.get.side_effect = [empty_pages, post_response]

        result = await client.resolve_page_id("mi-post")
        assert result == 77
        assert mock_http.get.call_count == 2

    async def test_slug_not_found_anywhere_raises(self, client, mock_http):
        """Si el slug no existe en pages ni posts, debe lanzar WordPressPageNotFoundError."""
        mock_http.get.return_value = _make_response(200, [])
        with pytest.raises(WordPressPageNotFoundError):
            await client.resolve_page_id("pagina-inexistente")

    async def test_auth_error_on_resolve_raises_immediately(self, client, mock_http):
        """Un 401 durante la resolución debe lanzar WordPressAuthError."""
        mock_http.get.return_value = _make_response(401)
        with pytest.raises(WordPressAuthError):
            await client.resolve_page_id("cualquier-slug")


# ── Tests de get_page_by_id ────────────────────────────────────────────────────

class TestGetPageById:

    async def test_returns_page_content_on_success(self, client, mock_http):
        page_data = _make_page_data(
            page_id=42,
            slug="consulta",
            raw_content="<h2>Título</h2><p>Contenido.</p>",
        )
        mock_http.get.return_value = _make_response(200, page_data)
        result = await client.get_page_by_id(42)

        assert result.page_id == 42
        assert result.slug == "consulta"
        assert "<h2>Título</h2>" in result.raw_content
        assert result.content_type == "page"

    async def test_maps_post_content_type_correctly(self, client, mock_http):
        """Un post debe tener content_type='post'."""
        not_a_page = _make_response(404)
        post_data = _make_page_data(page_id=10, content_type="post")
        post_response = _make_response(200, post_data)
        mock_http.get.side_effect = [not_a_page, post_response]

        result = await client.get_page_by_id(10)
        assert result.content_type == "post"

    async def test_page_not_found_in_pages_tries_posts(self, client, mock_http):
        """Si /pages/{id} da 404, debe intentar /posts/{id}."""
        mock_http.get.side_effect = [
            _make_response(404),                    # Not in pages
            _make_response(200, _make_page_data()),  # Found in posts
        ]
        result = await client.get_page_by_id(42)
        assert result.page_id == 42
        assert mock_http.get.call_count == 2

    async def test_not_found_anywhere_raises(self, client, mock_http):
        mock_http.get.return_value = _make_response(404)
        with pytest.raises(WordPressPageNotFoundError, match="not found"):
            await client.get_page_by_id(999)

    async def test_401_raises_auth_error(self, client, mock_http):
        mock_http.get.return_value = _make_response(401)
        with pytest.raises(WordPressAuthError, match="Authentication failed"):
            await client.get_page_by_id(42)

    async def test_403_raises_auth_error(self, client, mock_http):
        mock_http.get.return_value = _make_response(403)
        with pytest.raises(WordPressAuthError, match="forbidden"):
            await client.get_page_by_id(42)

    async def test_500_raises_api_error(self, client, mock_http):
        mock_http.get.return_value = _make_response(500)
        with pytest.raises(WordPressAPIError):
            await client.get_page_by_id(42)

    async def test_uses_context_edit_param(self, client, mock_http):
        """La llamada GET debe incluir context=edit para obtener contenido RAW."""
        mock_http.get.return_value = _make_response(200, _make_page_data())
        await client.get_page_by_id(42)

        call_params = mock_http.get.call_args[1]["params"]
        assert call_params.get("context") == "edit"


# ── Tests de update_page ───────────────────────────────────────────────────────

class TestUpdatePage:

    async def test_successful_update_returns_true(self, client, mock_http):
        mock_http.post.return_value = _make_response(200, _make_page_data())
        result = await client.update_page(42, "<p>Nuevo contenido.</p>", content_type="page")
        assert result is True

    async def test_sends_content_in_payload(self, client, mock_http):
        """El payload POST debe incluir el campo 'content' con el HTML nuevo."""
        mock_http.post.return_value = _make_response(200, _make_page_data())
        new_html = "<p>Contenido actualizado para WordPress.</p>"
        await client.update_page(42, new_html, content_type="page")

        call_json = mock_http.post.call_args[1]["json"]
        assert call_json["content"] == new_html

    async def test_falls_back_to_posts_if_page_404(self, client, mock_http):
        """Si la actualización de /pages da 404, debe intentar /posts."""
        mock_http.post.side_effect = [
            _make_response(404),            # pages da 404
            _make_response(200, _make_page_data()),  # posts OK
        ]
        result = await client.update_page(42, "<p>Contenido.</p>", content_type="page")
        assert result is True
        assert mock_http.post.call_count == 2

    async def test_not_found_in_any_type_raises(self, client, mock_http):
        mock_http.post.return_value = _make_response(404)
        with pytest.raises(WordPressPageNotFoundError):
            await client.update_page(999, "<p>Contenido.</p>")

    async def test_401_on_update_raises_auth_error(self, client, mock_http):
        mock_http.post.return_value = _make_response(401)
        with pytest.raises(WordPressAuthError):
            await client.update_page(42, "<p>Contenido.</p>")

    async def test_content_type_post_tries_posts_endpoint_first(self, client, mock_http):
        """Si content_type='post', el primer intento debe ser contra /posts."""
        mock_http.post.return_value = _make_response(200, _make_page_data())
        await client.update_page(42, "<p>Contenido.</p>", content_type="post")

        first_call_url = mock_http.post.call_args_list[0][0][0]
        assert "/posts/" in first_call_url


# ── Tests de _extract_slug_from_url ───────────────────────────────────────────

class TestExtractSlugFromUrl:
    """Tests para la función de extracción de slug de URL."""

    def test_simple_url(self, client):
        assert client._extract_slug_from_url("https://site.com/about") == "about"

    def test_url_with_trailing_slash(self, client):
        assert client._extract_slug_from_url("https://site.com/about/") == "about"

    def test_nested_path_url(self, client):
        assert (
            client._extract_slug_from_url("https://site.com/servicios/consulta/")
            == "consulta"
        )

    def test_url_with_no_path_raises(self, client):
        with pytest.raises(ValueError, match="slug"):
            client._extract_slug_from_url("https://site.com/")
