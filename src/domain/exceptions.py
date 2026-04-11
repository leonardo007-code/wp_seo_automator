from __future__ import annotations


class WpAutoError(Exception):
    """Excepción base del sistema. Nunca lanzar esta directamente."""


class WordPressAuthError(WpAutoError):
    """
    Fallo de autenticación o autorización con WordPress.
    Causas: credenciales incorrectas (401) o permisos insuficientes (403).
    """


class WordPressPageNotFoundError(WpAutoError):
    """
    La página o post solicitado no existe en WordPress.
    Puede ocurrir al resolver un slug/URL o al obtener por ID.
    """


class WordPressAPIError(WpAutoError):
    """
    Error inesperado de la API de WordPress (5xx, respuesta malformada, etc.).
    Incluye el status code y fragmento de la respuesta en el mensaje.
    """


class ContentIntegrityError(WpAutoError):
    """
    El HTML reconstruido falló la validación de integridad estructural.
    El sistema se negó a publicar para proteger la página.
    """


class LLMProviderError(WpAutoError):
    """
    El proveedor LLM devolvió un error irrecuperable tras todos los reintentos.
    """
