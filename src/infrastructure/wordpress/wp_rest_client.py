from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from src.config.settings import Settings
from src.domain.entities import PageContent
from src.domain.exceptions import (
    WordPressAPIError,
    WordPressAuthError,
    WordPressPageNotFoundError,
)

logger = logging.getLogger(__name__)

# WordPress REST API content types we support.
# Order matters: "pages" is tried before "posts" in all lookups.
_CONTENT_TYPES = ("pages", "posts")


class WpRestClient:
    """
    Implements IWordPressClient.

    Communicates with the WordPress REST API using HTTP Basic Auth
    via Application Passwords (native WP feature since 5.6).

    Design decisions:
    ─────────────────
    - Single httpx.AsyncClient instance per WpRestClient (connection reuse).
    - Injectable http_client for testing (no extra mock libraries needed).
    - Always uses context=edit to get RAW content (not rendered HTML with WP filters).
    - Tries /pages before /posts transparently — caller never deals with this.
    - All HTTP errors mapped to domain exceptions — no httpx exceptions leak out.
    """

    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_base = f"{settings.wp_base_url}/wp-json/wp/v2"
        auth = httpx.BasicAuth(
            username=settings.wp_api_user,
            # WP Application Passwords include spaces — httpx.BasicAuth handles encoding
            password=settings.wp_api_app_password,
        )
        self._http = http_client or httpx.AsyncClient(
            auth=auth,
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._http.aclose()

    # ── Public API (implements IWordPressClient) ───────────────────────────────

    async def resolve_page_id(self, identifier: str) -> int:
        """
        Resolves any identifier format to a WordPress internal page/post ID.

        Priority order:
        1. Pure numeric string → return as int directly (no API call needed).
        2. URL (starts with http/https) → extract slug from path, then search.
        3. Anything else → treat as slug, search pages then posts.

        Raises:
            WordPressPageNotFoundError: if no matching page or post is found.
        """
        cleaned = identifier.strip()

        if cleaned.isdigit():
            logger.debug("Identifier is numeric — using directly", extra={"id": cleaned})
            return int(cleaned)

        if cleaned.startswith(("http://", "https://")):
            slug = self._extract_slug_from_url(cleaned)
            logger.debug("Extracted slug from URL", extra={"url": cleaned, "slug": slug})
        else:
            slug = cleaned

        for content_type in _CONTENT_TYPES:
            endpoint = f"{self._api_base}/{content_type}"
            response = await self._http.get(
                endpoint,
                params={"slug": slug, "context": "edit"},
            )
            self._handle_auth_errors(response)

            if response.status_code == 200:
                results = response.json()
                if results:
                    page_id = results[0]["id"]
                    logger.info(
                        "Resolved identifier to page ID",
                        extra={
                            "identifier": identifier,
                            "slug": slug,
                            "page_id": page_id,
                            "content_type": content_type,
                        },
                    )
                    return page_id

        raise WordPressPageNotFoundError(
            f"No page or post found for identifier: {identifier!r}. "
            f"Searched slug={slug!r} in pages and posts."
        )

    async def get_page_by_id(self, page_id: int) -> PageContent:
        """
        Fetches the full page/post content by ID.

        Uses context=edit to retrieve content.raw (unrendered Gutenberg blocks).
        This is critical — content.rendered has WP filters applied and cannot
        be safely re-posted.

        Tries /pages first, then /posts.

        Raises:
            WordPressPageNotFoundError: if the ID doesn't exist.
            WordPressAuthError: if credentials are wrong or lack edit permission.
            WordPressAPIError: on unexpected API errors.
        """
        for content_type in _CONTENT_TYPES:
            endpoint = f"{self._api_base}/{content_type}/{page_id}"
            response = await self._http.get(
                endpoint,
                params={"context": "edit"},
            )

            if response.status_code == 200:
                data = response.json()
                page = self._map_to_page_content(data, content_type)
                logger.info(
                    "Fetched page content",
                    extra={
                        "page_id": page_id,
                        "slug": page.slug,
                        "content_type": content_type,
                        "content_length": len(page.raw_content),
                    },
                )
                return page

            if response.status_code == 404:
                # Not in this content type — try the next one
                continue

            # Any other status is an error we must surface
            self._raise_for_status(response)

        raise WordPressPageNotFoundError(
            f"Page with ID {page_id} not found in pages or posts."
        )

    async def update_page(
        self,
        page_id: int,
        new_content: str,
        content_type: str = "page",
    ) -> bool:
        """
        Publishes new raw content to a WordPress page or post.

        Prefers the provided content_type to avoid a redundant lookup.
        Falls back to the other type if a 404 is returned.

        The WP REST API expects a POST (not PUT) to update a resource.
        Payload: { "content": "<raw html>" }

        Raises:
            WordPressPageNotFoundError: if the ID is not found in any content type.
            WordPressAuthError: if credentials lack edit permission.
            WordPressAPIError: on unexpected API errors.
        """
        # Normalize: accept both "page"/"pages" and "post"/"posts"
        # Derive the canonical endpoint names for primary and fallback.
        base_type = content_type.rstrip("s")           # "page" → "page", "pages" → "page"
        primary_endpoint = f"{base_type}s"             # "pages" or "posts"
        fallback_endpoint = "posts" if base_type == "page" else "pages"
        types_to_try = [primary_endpoint, fallback_endpoint]


        payload = {"content": new_content}

        for ct in types_to_try:
            endpoint = f"{self._api_base}/{ct}/{page_id}"
            response = await self._http.post(endpoint, json=payload)


            if response.status_code == 200:
                logger.info(
                    "Page updated successfully",
                    extra={"page_id": page_id, "content_type": ct},
                )
                return True

            if response.status_code == 404:
                logger.debug(
                    "Page not found in content type, trying next",
                    extra={"page_id": page_id, "content_type": ct},
                )
                continue

            self._raise_for_status(response)

        raise WordPressPageNotFoundError(
            f"Cannot update: no page or post found with ID {page_id}."
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_slug_from_url(url: str) -> str:
        """
        Extracts the WordPress slug (last path segment) from a public URL.

        Examples:
            https://site.com/servicios/consulting-empresarial/ → "consulting-empresarial"
            https://site.com/about-us → "about-us"

        Raises:
            ValueError: if the URL has no extractable path segment.
        """
        parsed = urlparse(url)
        segments = [s for s in parsed.path.rstrip("/").split("/") if s]
        if not segments:
            raise ValueError(
                f"Cannot extract slug from URL — path is empty: {url!r}"
            )
        return segments[-1]

    @staticmethod
    def _map_to_page_content(data: dict, content_type: str) -> PageContent:
        """
        Maps a raw WP REST API response dict to our PageContent domain entity.

        NOTE: We use data["content"]["raw"] — this requires context=edit.
        If "raw" is missing, it means the request was made without authentication
        or without the edit context.
        """
        raw = data.get("content", {}).get("raw", "")
        if not raw:
            logger.warning(
                "content.raw is empty — request may lack context=edit or authentication",
                extra={"page_id": data.get("id")},
            )

        return PageContent(
            page_id=data["id"],
            slug=data.get("slug", ""),
            title=data.get("title", {}).get("rendered", ""),
            raw_content=raw,
            url=data.get("link", ""),
            last_modified=data.get("modified", ""),
            content_type="page" if content_type == "pages" else "post",
        )

    @staticmethod
    def _handle_auth_errors(response: httpx.Response) -> None:
        """Raises domain exceptions for authentication errors (401/403)."""
        if response.status_code == 401:
            raise WordPressAuthError(
                "Authentication failed. Verify WP_API_USER and WP_API_APP_PASSWORD. "
                "Make sure Application Passwords are enabled in WordPress."
            )
        if response.status_code == 403:
            raise WordPressAuthError(
                "Access forbidden. The configured user may lack 'edit_pages' permission."
            )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """
        Maps HTTP error codes to domain exceptions.
        Only call this for non-404 errors — 404s are handled per content type.
        """
        if response.status_code == 401:
            raise WordPressAuthError(
                "Authentication failed. Verify WP_API_USER and WP_API_APP_PASSWORD."
            )
        if response.status_code == 403:
            raise WordPressAuthError(
                "Access forbidden. The user may lack edit permissions."
            )
        raise WordPressAPIError(
            f"WordPress API returned HTTP {response.status_code}. "
            f"Response: {response.text[:300]}"
        )
