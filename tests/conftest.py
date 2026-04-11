"""
conftest.py de nivel raíz.

Establece variables de entorno requeridas ANTES de que cualquier módulo
de la aplicación sea importado. Si esto no se hace aquí, pydantic-settings
puede fallar al crear Settings() durante los imports de test.

Este archivo es cargado por pytest antes de coleccionar cualquier test.
"""
import os

# Estos valores son falsos y solo sirven para que Settings() pueda instanciarse.
# Los tests que necesitan comportamiento real mockean las dependencias directamente.
os.environ.setdefault("WP_BASE_URL", "https://test.example.com")
os.environ.setdefault("WP_API_USER", "test_admin")
os.environ.setdefault("WP_API_APP_PASSWORD", "test xxxx xxxx xxxx")
os.environ.setdefault("GEMINI_API_KEY", "fake-key-for-testing")
os.environ.setdefault("LLM_BACKEND", "gemini")
os.environ.setdefault("DRY_RUN_DEFAULT", "true")
