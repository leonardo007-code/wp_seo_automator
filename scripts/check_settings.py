"""
check_settings.py — Verificación de configuración del sistema.

Comprueba:
  1. Que el archivo .env existe en la raíz del proyecto
  2. Que todas las variables obligatorias están cargadas
  3. Que las variables opcionales tienen valores razonables
  4. Que Gemini puede inicializarse con la API key configurada
  5. Sin imprimir secretos completos en consola (solo los últimos 4 chars)

Uso:
  python scripts/check_settings.py
  python scripts/check_settings.py --strict   # exit(1) si algo falla

NO requiere que el servidor esté corriendo.
NO hace llamadas a la API de Gemini.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Asegurar que el root del proyecto esté en el path de Python
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Forzar UTF-8 en stdout para Windows (evita UnicodeEncodeError con CP1252)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _ok(label: str, value: str = "") -> None:
    suffix = f"  [{value}]" if value else ""
    print(f"  [OK]   {label}{suffix}")


def _fail(label: str, hint: str = "") -> None:
    print(f"  [FAIL] {label}")
    if hint:
        print(f"         --> {hint}")


def _warn(label: str, hint: str = "") -> None:
    print(f"  [WARN] {label}")
    if hint:
        print(f"         --> {hint}")


def _section(title: str) -> None:
    print(f"\n--- {title} ---")


def _mask(value: str, show_last: int = 4) -> str:
    """Muestra solo los últimos N caracteres de un secreto."""
    if not value or len(value) <= show_last:
        return "(vacio)"
    return ("*" * min(8, len(value) - show_last)) + value[-show_last:]


# ── Check 1: Existencia del .env ───────────────────────────────────────────────


def check_env_file() -> bool:
    _section("1. Archivo .env")
    env_path = ROOT / ".env"

    if env_path.exists():
        size = env_path.stat().st_size
        _ok(f".env encontrado en la raiz del proyecto", f"{size} bytes")
        return True
    else:
        _fail(
            f".env NO encontrado en: {env_path}",
            hint="Ejecuta en la raiz del proyecto:\n"
                 "         Windows:    copy .env.example .env\n"
                 "         Linux/Mac:  cp .env.example .env\n"
                 "         Luego abre .env y completa tus credenciales reales.",
        )
        return False


# ── Check 2: Carga de Settings ─────────────────────────────────────────────────


def check_settings_load() -> tuple[bool, object | None]:
    _section("2. Carga de configuracion")

    try:
        from src.config.settings import get_settings
        get_settings.cache_clear()
        settings = get_settings()
    except Exception as e:
        _fail(f"Error al cargar Settings: {e}")
        return False, None

    _ok("Settings cargado correctamente")

    # ── Variables obligatorias ─────────────────────────────────────────────────
    print("\n  Variables obligatorias:")
    all_required_ok = True

    placeholder_wp = {"https://tusitio.com", "", None}
    placeholder_user = {"tu_usuario_wp", "<COMPLETAR>", "", None}
    placeholder_pass = {"xxxx xxxx xxxx xxxx", "<COMPLETAR: xxxx xxxx xxxx xxxx>", "", None}

    checks = [
        ("WP_BASE_URL",         settings.wp_base_url,          settings.wp_base_url not in placeholder_wp),
        ("WP_API_USER",         settings.wp_api_user,          settings.wp_api_user not in placeholder_user),
        ("WP_API_APP_PASSWORD", _mask(settings.wp_api_app_password), settings.wp_api_app_password not in placeholder_pass),
    ]

    for name, display_value, is_real in checks:
        if not display_value or display_value == "(vacio)":
            _fail(f"{name} esta vacio", f"Agrega {name} a tu .env")
            all_required_ok = False
        elif not is_real:
            _warn(f"{name} = {display_value}", "Parece un placeholder -- completa el .env con el valor real")
        else:
            _ok(f"{name}", display_value)

    # ── Variables opcionales ───────────────────────────────────────────────────
    print("\n  Variables opcionales (con sus valores actuales):")
    optional_info = [
        ("APP_ENV",                   settings.app_env.value),
        ("APP_HOST",                  settings.app_host),
        ("APP_PORT",                  str(settings.app_port)),
        ("DEBUG_MODE",                str(settings.debug_mode)),
        ("LLM_BACKEND",               settings.llm_backend.value),
        ("GEMINI_MODEL",              settings.gemini_model),
        ("DRY_RUN_DEFAULT",           str(settings.dry_run_default)),
        ("BACKUP_DIR",                str(settings.backup_dir)),
        ("LOG_DIR",                   str(settings.log_dir)),
        ("LOG_LEVEL",                 settings.log_level),
        ("REQUEST_TIMEOUT_SECONDS",   str(settings.request_timeout_seconds)),
        ("MAX_RETRIES",               str(settings.max_retries)),
    ]
    for name, value in optional_info:
        _ok(f"{name}", value)

    # ── GEMINI_API_KEY (secreto — enmascarado) ─────────────────────────────────
    print("\n  Credenciales LLM (enmascaradas):")
    placeholder_gemini = {"AIza...", "<COMPLETAR: AIzaSy...>", "fake-key-for-testing", ""}
    if settings.gemini_api_key in placeholder_gemini:
        if settings.llm_backend.value == "gemini":
            _fail("GEMINI_API_KEY esta vacia o es un placeholder",
                  "Necesaria si LLM_BACKEND=gemini -- agrega la key real al .env")
            all_required_ok = False
        else:
            _warn("GEMINI_API_KEY no configurada",
                  f"No necesaria con LLM_BACKEND={settings.llm_backend.value}")
    else:
        _ok("GEMINI_API_KEY", _mask(settings.gemini_api_key, show_last=6))

    # ── Advertencias de configuracion ─────────────────────────────────────────
    if settings.app_env.value == "production" and settings.debug_mode:
        _warn("DEBUG_MODE=true con APP_ENV=production",
              "Deshabilita DEBUG_MODE en produccion")

    if not settings.dry_run_default:
        _warn("DRY_RUN_DEFAULT=false",
              "El sistema publicara cambios en WordPress por defecto. "
              "Asegurate de que es intencional.")

    return all_required_ok, settings


# ── Check 3: Inicializacion de Gemini ─────────────────────────────────────────


def check_gemini_init(settings) -> bool:
    _section("3. SDK de Gemini")

    placeholder_gemini = {"AIza...", "<COMPLETAR: AIzaSy...>", "fake-key-for-testing", ""}
    if settings.gemini_api_key in placeholder_gemini:
        _warn("GEMINI_API_KEY no configurada o es placeholder -- saltando verificacion")
        return True

    try:
        from google import genai
        genai.Client(api_key=settings.gemini_api_key)
        _ok(
            "SDK de Gemini inicializado correctamente",
            f"key: {_mask(settings.gemini_api_key, show_last=6)}  model: {settings.gemini_model}",
        )
        _warn(
            "Esta verificacion NO hace una llamada de red a la API.",
            "Usa 'python scripts/validate.py --only=gemini' para probar conectividad real."
        )
        return True
    except Exception as e:
        _fail(f"Error al inicializar SDK de Gemini: {e}")
        return False


# ── Check 4: Directorios de storage ───────────────────────────────────────────


def check_storage(settings) -> bool:
    _section("4. Directorios de storage")

    for name, path in [("BACKUP_DIR", settings.backup_dir), ("LOG_DIR", settings.log_dir)]:
        if path.exists():
            _ok(f"{name}", str(path))
        else:
            _warn(
                f"{name} no existe: {path}",
                "Se creara automaticamente al arrancar el servidor."
            )
    return True


# ── Resumen ────────────────────────────────────────────────────────────────────


def print_summary(results: dict[str, bool]) -> bool:
    _section("RESUMEN")
    labels = {
        "env_file": "Archivo .env encontrado",
        "settings": "Variables cargadas correctamente",
        "gemini":   "SDK de Gemini inicializable",
        "storage":  "Directorios de storage",
    }
    all_ok = True
    for key, ok in results.items():
        symbol = "PASS" if ok else "FAIL"
        print(f"  [{symbol}]  {labels.get(key, key)}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("Configuracion valida. El sistema esta listo para arrancar.")
        print("Siguiente paso:")
        print("  .venv\\Scripts\\uvicorn src.main:app --reload")
    else:
        print("Hay problemas en la configuracion. Revisa los mensajes anteriores.")

    return all_ok


# ── Main ───────────────────────────────────────────────────────────────────────


def main(strict: bool = False) -> None:
    print("\nWP SEO Automator -- Verificacion de Configuracion")
    print("=" * 52)

    results: dict[str, bool] = {}

    results["env_file"] = check_env_file()

    if results["env_file"]:
        ok, settings = check_settings_load()
        results["settings"] = ok
    else:
        results["settings"] = False
        settings = None
        print("\n  Saltando checks 3 y 4 -- no hay .env que cargar.")

    if settings is not None:
        results["gemini"] = check_gemini_init(settings)
        results["storage"] = check_storage(settings)
    else:
        results["gemini"] = False
        results["storage"] = False

    all_ok = print_summary(results)

    if strict and not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verifica la configuracion de WP SEO Automator."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Termina con exit code 1 si hay algun problema (util en CI).",
    )
    args = parser.parse_args()
    main(strict=args.strict)
