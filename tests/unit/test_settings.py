from __future__ import annotations

from src.config.settings import AppEnv, LLMBackend, Settings, get_settings


def test_settings_loads_minimum_required_values(tmp_path):
    settings = Settings(
        wp_base_url="https://example.com/",
        wp_api_user="user",
        wp_api_app_password="pass",
        backup_dir=tmp_path / "backups",
        log_dir=tmp_path / "logs",
    )
    assert settings.wp_base_url == "https://example.com"
    assert settings.llm_backend == LLMBackend.GEMINI
    assert settings.app_env == AppEnv.DEVELOPMENT


def test_settings_validates_log_level(tmp_path):
    try:
        Settings(
            wp_base_url="https://example.com",
            wp_api_user="user",
            wp_api_app_password="pass",
            backup_dir=tmp_path / "backups",
            log_dir=tmp_path / "logs",
            log_level="INVALID",
        )
        assert False, "Expected validation error for invalid log level"
    except Exception as exc:
        assert "LOG_LEVEL must be one of" in str(exc)


def test_get_settings_is_cached():
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
