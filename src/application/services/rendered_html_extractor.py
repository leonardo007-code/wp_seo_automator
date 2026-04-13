"""
rendered_html_extractor.py — Extractor de texto desde HTML renderizado.

Responsabilidad: para builders que almacenan su data en post meta
(Elementor, Oxygen, Breakdance, Bricks), el content.raw de la REST API
no contiene texto editable. Este módulo hace un GET HTTP al URL público
de la página para obtener el HTML renderizado y extrae texto visible.

IMPORTANTE — Limitación de publicación:
    Este modo es SIEMPRE analysis_only (ANALYSIS_ONLY policy).
    NO se puede publicar de vuelta vía REST API content.raw porque
    el contenido real de estos builders vive en post meta JSON.
    Publicar solo sirve para análisis, preview y validación SEO.

Texto extraído:
    - Headings (h1-h6)
    - Párrafos (p)
    - Listas (li)
    - Botones con texto
    - Texto de tabs/acordeones visible en el DOM

No se extrae:
    - Atributos data-*, clases, IDs
    - Alt text (salvo que sea explícitamente útil para SEO)
    - Texto oculto (display:none via style inline)
    - Scripts, estilos, iframes

Timeout: configurable, default 20s. El fetch es síncrono dentro del
contexto async vía httpx.AsyncClient.
"""
from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup, Tag

from src.domain.entities import EditableSegment

logger = logging.getLogger(__name__)

MIN_TEXT_LENGTH = 15

# Tags cuyo texto directo es editable
EDITABLE_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "blockquote", "figcaption", "td", "th", "button",
})

# Clases CSS que indican contenido de navegación, footer, header — excluir
_SKIP_CLASS_PATTERNS = re.compile(
    r"(nav|navbar|menu|breadcrumb|footer|header|cookie|modal|"
    r"sidebar|widget|admin|toolbar|wpadminbar)",
    re.IGNORECASE,
)

# Atributos que indican que el elemento está oculto
_HIDDEN_STYLE_PATTERN = re.compile(r"display\s*:\s*none", re.IGNORECASE)


class RenderedHTMLExtractor:
    """
    Extrae texto visible del HTML público renderizado de una página WordPress.

    Solo para builders donde content.raw no es editable directamente
    (Elementor, Oxygen, Breakdance, Bricks).

    Este extractor NO soporta reconstrucción — es analysis_only.

    Uso:
        extractor = RenderedHTMLExtractor(timeout=20)
        segments = await extractor.extract_from_url(page_url)
    """

    def __init__(self, timeout: int = 20) -> None:
        self._timeout = timeout

    async def extract_from_url(self, url: str) -> list[EditableSegment]:
        """
        Hace GET al URL público de la página y extrae segmentos de texto.

        Args:
            url: URL pública de la página WordPress.

        Returns:
            Lista de EditableSegment con el texto visible.
            Retorna [] si la página no es accesible o no tiene texto.

        Note:
            Este método nunca lanza excepciones. Los errores de red se
            loggean como warnings y se retorna lista vacía.
        """
        try:
            html = await self._fetch_html(url)
            return self._parse_segments(html)
        except Exception as e:
            logger.warning(
                "RenderedHTMLExtractor failed — returning empty segments",
                extra={"url": url, "error": str(e)},
            )
            return []

    def extract_from_html(self, html: str) -> list[EditableSegment]:
        """
        Extrae segmentos directamente desde un string HTML ya obtenido.
        Útil para tests y para cuando el HTML ya fue obtenido por otro medio.
        """
        return self._parse_segments(html)

    async def _fetch_html(self, url: str) -> str:
        """
        GET HTTP al URL con un User-Agent de scraper común.
        No envía credenciales de WordPress.
        """
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

    def _parse_segments(self, html: str) -> list[EditableSegment]:
        """
        Parsea el HTML renderizado y extrae segmentos de texto editable.

        Estrategia:
          1. Parsear con BeautifulSoup (lxml si disponible, html.parser como fallback)
          2. Eliminar <script>, <style>, <noscript>, <header> nav, <footer>
          3. Buscar tags editables con texto directo
          4. Filtrar por longitud mínima y texto oculto
        """
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")

        # Eliminar bloques que nunca son contenido editorial
        for tag_name in ("script", "style", "noscript", "meta", "link", "head"):
            for el in soup.find_all(tag_name):
                el.decompose()

        # Eliminar elementos de navegación/header/footer por clase
        for el in soup.find_all(True):
            classes = " ".join(el.get("class", []))
            if _SKIP_CLASS_PATTERNS.search(classes):
                el.decompose()

        segments: list[EditableSegment] = []
        index = 0
        seen_texts: set[str] = set()  # deduplicar textos repetidos

        for element in soup.find_all(EDITABLE_TAGS):
            # Solo texto directo (element.string = None si hay hijos-tag)
            if element.string is None:
                # Para elementos con mixed content, intentar get_text()
                text = element.get_text(separator=" ", strip=True)
            else:
                text = element.string.strip()

            if not text or len(text) < MIN_TEXT_LENGTH:
                continue

            # Omitir duplicados exactos (menus repetidos, por ejemplo)
            if text in seen_texts:
                continue

            # Omitir elementos con style="display:none"
            style = element.get("style", "")
            if _HIDDEN_STYLE_PATTERN.search(style):
                continue

            # Omitir si es solo un número o código
            if re.fullmatch(r'[\d\s\+\-\(\)\/\.]+', text):
                continue

            seen_texts.add(text)
            segments.append(EditableSegment(
                index=index,
                tag=element.name,
                text=text,
            ))
            index += 1

        logger.info(
            "RenderedHTML extraction complete",
            extra={"segments_found": len(segments)},
        )

        return segments
