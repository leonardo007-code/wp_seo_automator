"""
conftest.py de nivel raíz.

Establece variables de entorno requeridas ANTES de que cualquier módulo
de la aplicación sea importado. Si esto no se hace aquí, pydantic-settings
puede fallar al crear Settings() durante los imports de test.

IMPORTANTE: setdefault() respeta las variables ya presentes en el entorno.
Si tienes un .env real, las variables ya cargadas no se sobreescriben.

Este archivo es cargado por pytest antes de coleccionar cualquier test.
"""
import os

# ── Variables obligatorias (sin default en Settings) ─────────────────────────
# Valores falsos — solo sirven para que Settings() pueda instanciarse en tests.
# Los tests que necesitan comportamiento real mockean las dependencias directamente.
os.environ.setdefault("WP_BASE_URL", "https://test.example.com")
os.environ.setdefault("WP_API_USER", "test_admin")
os.environ.setdefault("WP_API_APP_PASSWORD", "test xxxx xxxx xxxx")

# ── LLM ───────────────────────────────────────────────────────────────────────
os.environ.setdefault("GEMINI_API_KEY", "fake-key-for-testing")
os.environ.setdefault("LLM_BACKEND", "gemini")

# ── Comportamiento ─────────────────────────────────────────────────────────────
os.environ.setdefault("DRY_RUN_DEFAULT", "true")

# ── Entorno de ejecución (nuevas variables) ────────────────────────────────────
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_HOST", "127.0.0.1")
os.environ.setdefault("APP_PORT", "8000")
os.environ.setdefault("DEBUG_MODE", "false")
