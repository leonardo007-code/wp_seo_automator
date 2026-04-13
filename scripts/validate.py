"""
validate.py — Script de validación de conectividad real.

Ejecuta verificaciones en secuencia sin levantar el servidor FastAPI:
  1. Carga de .env y configuración
  2. Conectividad de red con WordPress (GET público)
  3. Autenticación WP con Application Password
  4. API Key de Gemini (genera un texto corto de prueba)
  5. Flujo completo dry_run (sin publicar nada)

Uso:
  python scripts/validate.py                  # todas las validaciones
  python scripts/validate.py --only=wp        # solo WordPress
  python scripts/validate.py --only=gemini    # solo Gemini
  python scripts/validate.py --only=env       # solo configuración

SEGURIDAD: Este script NUNCA publica cambios en WordPress.
El dry_run está hardcodeado a True para la validación del flujo completo.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# ── Asegurar que el root del proyecto esté en el path ─────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── Colores ANSI ───────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def _section(title: str) -> None:
    print(f"\n{BOLD}{BLUE}━━ {title} ━━{RESET}")


def _load_dotenv() -> None:
    """Carga el .env desde la raíz del proyecto antes de importar Settings."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    with env_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ── Checks ─────────────────────────────────────────────────────────────────────


def check_env() -> bool:
    """Valida que el .env existe y que todas las variables requeridas están presentes."""
    _section("1. Configuración (.env)")

    env_file = ROOT / ".env"
    if not env_file.exists():
        _fail(f".env no encontrado en {ROOT}")
        _warn("Copia .env.example a .env y completa las credenciales.")
        return False
    _ok(f".env encontrado: {env_file}")

    _load_dotenv()

    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
        settings = get_settings()
    except Exception as e:
        _fail(f"Error al cargar Settings: {e}")
        return False

    _ok(f"WP_BASE_URL       = {settings.wp_base_url}")
    _ok(f"WP_API_USER       = {settings.wp_api_user}")
    _ok(f"LLM_BACKEND       = {settings.llm_backend.value}")
    _ok(f"DRY_RUN_DEFAULT   = {settings.dry_run_default}")
    _ok(f"BACKUP_DIR        = {settings.backup_dir}")
    _ok(f"LOG_DIR           = {settings.log_dir}")

    has_gemini_key = bool(settings.gemini_api_key and settings.gemini_api_key != "AIza...")
    if settings.llm_backend.value == "gemini" and not has_gemini_key:
        _warn("GEMINI_API_KEY parece estar vacío o con el valor placeholder. Gemini no funcionará.")
    else:
        _ok(f"GEMINI_API_KEY    = {'*' * 8}...{settings.gemini_api_key[-4:] if settings.gemini_api_key else '(vacía)'}")

    return True


async def check_wordpress() -> bool:
    """Verifica conectividad y autenticación con WordPress."""
    _section("2. WordPress REST API")

    _load_dotenv()
    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        from src.infrastructure.wordpress.wp_rest_client import WpRestClient
    except Exception as e:
        _fail(f"No se pudo importar WpRestClient: {e}")
        return False

    import httpx

    # 2a. Conectividad de red pública (sin autenticación)
    api_url = f"{settings.wp_base_url}/wp-json/wp/v2"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as http:
            resp = await http.get(api_url)
        if resp.status_code in (200, 401):
            _ok(f"WordPress REST API accesible: {api_url} → HTTP {resp.status_code}")
        else:
            _fail(f"WordPress devolvió HTTP {resp.status_code} para {api_url}")
            return False
    except httpx.ConnectError as e:
        _fail(f"No se puede conectar a WordPress: {e}")
        _warn("Verifica que WP_BASE_URL sea correcto y que el servidor esté activo.")
        return False
    except Exception as e:
        _fail(f"Error de red inesperado: {e}")
        return False

    # 2b. Autenticación con Application Password
    client = WpRestClient(settings)
    try:
        pages_url = f"{settings.wp_base_url}/wp-json/wp/v2/pages"
        resp = await client._http.get(pages_url, params={"context": "edit", "per_page": 3})
        if resp.status_code == 200:
            pages = resp.json()
            _ok(f"Autenticación exitosa. Páginas encontradas: {len(pages)}")
            for p in pages[:3]:
                _ok(f"  → [{p['id']}] {p.get('slug', '?')} — {p.get('title', {}).get('rendered', '?')[:50]}")
        elif resp.status_code == 401:
            _fail("Error de autenticación (401). Verifica WP_API_USER y WP_API_APP_PASSWORD.")
            _warn("La Application Password debe generarse desde: WP Admin → Usuarios → Perfil → Contraseñas de Aplicación")
            return False
        elif resp.status_code == 403:
            _fail("Acceso denegado (403). El usuario no tiene permiso 'edit_pages'.")
            return False
        else:
            _fail(f"Respuesta inesperada de WordPress: HTTP {resp.status_code}")
            return False
    except Exception as e:
        _fail(f"Error autenticando con WordPress: {e}")
        return False
    finally:
        await client.close()

    return True


async def check_gemini() -> bool:
    """Verifica que la API key de Gemini es válida con una llamada mínima."""
    _section("3. Gemini API")

    _load_dotenv()
    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
        settings = get_settings()
    except Exception as e:
        _fail(f"No se pudo cargar settings: {e}")
        return False

    if not settings.gemini_api_key or settings.gemini_api_key.startswith("AIza..."):
        _fail("GEMINI_API_KEY no está configurada o tiene el valor placeholder.")
        _warn("Genera una API key en: https://aistudio.google.com/app/apikey")
        return False

    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=settings.gemini_api_key)
        _ok(f"SDK de Gemini inicializado. Modelo: {settings.gemini_model}")

        # Llamada mínima — un solo token de respuesta para validar la key
        response = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents="Responde exactamente con: OK",
            config=genai_types.GenerateContentConfig(
                max_output_tokens=10,
                temperature=0.0,
            ),
        )
        text = response.text.strip() if response.text else ""
        _ok(f"Gemini respondió: {text!r}")
        return True

    except Exception as e:
        error_str = str(e).lower()
        if "api_key" in error_str or "invalid" in error_str or "401" in error_str:
            _fail(f"API Key inválida o expirada: {e}")
        elif "quota" in error_str or "429" in error_str:
            _warn(f"Quota excedida temporalmente: {e}")
        else:
            _fail(f"Error de Gemini: {e}")
        return False


async def check_dry_run(identifier: str | None = None) -> bool:
    """Ejecuta el flujo completo en dry_run contra una página real de WordPress."""
    _section("4. Flujo completo (dry_run=True, SIN publicar)")

    if not identifier:
        _warn("No se especificó --page. Usando la primera página encontrada en WordPress.")

    _load_dotenv()
    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        from src.infrastructure.wordpress.wp_rest_client import WpRestClient
        from src.infrastructure.providers.gemini_provider import GeminiProvider
        from src.application.services.content_protection import ContentProtectionService
        from src.application.services.diff_service import DiffService
        from src.infrastructure.repositories.local_backup_repo import LocalBackupRepository
        from src.application.use_cases.modify_page import ModifyPageUseCase
    except Exception as e:
        _fail(f"Error al importar módulos: {e}")
        return False

    wp_client = WpRestClient(settings)

    try:
        # Resolver la página
        if identifier:
            target = identifier
        else:
            # Buscar la primera página disponible
            resp = await wp_client._http.get(
                f"{settings.wp_base_url}/wp-json/wp/v2/pages",
                params={"context": "edit", "per_page": 1},
            )
            if resp.status_code != 200 or not resp.json():
                _fail("No se encontraron páginas en WordPress para probar el dry_run.")
                return False
            target = str(resp.json()[0]["id"])
            _ok(f"Usando página automática: ID={target}")

        use_case = ModifyPageUseCase(
            wp_client=wp_client,
            llm_provider=GeminiProvider(settings),
            protection_service=ContentProtectionService(),
            backup_repo=LocalBackupRepository(settings),
            diff_service=DiffService(),
        )

        _ok(f"Ejecutando dry_run para: {target!r}")
        _ok("Instrucción: 'Mejora ligeramente la claridad del texto (mínima modificación de prueba)'")

        result = await use_case.execute(
            identifier=target,
            instructions="Mejora ligeramente la claridad del texto (mínima modificación de prueba)",
            dry_run=True,  # SIEMPRE True en validate.py
        )

        _ok(f"Status           : {result.status.value}")
        _ok(f"Page ID          : {result.page_id}")
        _ok(f"Segmentos        : {result.segments_found} encontrados, {result.segments_modified} modificados")
        _ok(f"Backup           : {result.backup_path}")
        if result.warnings:
            for w in result.warnings:
                _warn(f"Warning: {w}")
        _ok("dry_run completado — NINGÚN cambio fue publicado en WordPress")

        return True

    except Exception as e:
        _fail(f"Error en el dry_run: {type(e).__name__}: {e}")
        return False
    finally:
        await wp_client.close()


# ── Main ───────────────────────────────────────────────────────────────────────


async def main(only: str | None, page: str | None) -> None:
    print(f"\n{BOLD}WP SEO Automator — Validación de Conectividad{RESET}")
    print("=" * 50)

    results: dict[str, bool] = {}

    if only in (None, "env"):
        results["env"] = check_env()

    if only in (None, "wp"):
        results["wp"] = await check_wordpress()

    if only in (None, "gemini"):
        results["gemini"] = await check_gemini()

    if only in (None, "dryrun"):
        if results.get("wp", True) and results.get("gemini", True):
            results["dryrun"] = await check_dry_run(page)
        else:
            _section("4. Flujo completo (dry_run)")
            _warn("Saltando dry_run porque WordPress o Gemini fallaron.")
            results["dryrun"] = False

    # ── Resumen final ──────────────────────────────────────────────────────────
    _section("RESUMEN")
    labels = {
        "env": "Configuración (.env)",
        "wp": "WordPress REST API",
        "gemini": "Gemini API",
        "dryrun": "Flujo dry_run",
    }
    all_ok = True
    for key, ok in results.items():
        symbol = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {symbol}  {labels.get(key, key)}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}✓ Todas las validaciones pasaron. El sistema está listo.{RESET}")
    else:
        print(f"{RED}{BOLD}✗ Una o más validaciones fallaron. Revisa los mensajes anteriores.{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Valida la conectividad y configuración de WP SEO Automator."
    )
    parser.add_argument(
        "--only",
        choices=["env", "wp", "gemini", "dryrun"],
        help="Ejecutar solo una validación específica.",
    )
    parser.add_argument(
        "--page",
        help="Identificador de la página WP para el dry_run (URL, slug o ID numérico).",
    )
    args = parser.parse_args()

    asyncio.run(main(only=args.only, page=args.page))
