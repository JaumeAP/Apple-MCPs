from apple_shortcuts_mcp.config import load_settings


def test_load_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("APPLE_SHORTCUTS_MCP_SAFETY_MODE", raising=False)
    monkeypatch.delenv("APPLE_SHORTCUTS_MCP_LOG_LEVEL", raising=False)
    monkeypatch.delenv("APPLE_SHORTCUTS_MCP_SHORTCUTS_COMMAND", raising=False)
    load_settings.cache_clear()

    settings = load_settings()

    assert settings.safety_mode == "full_access"
    assert settings.shortcuts_command == "shortcuts"


def test_safety_mode_is_normalized_and_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_SHORTCUTS_MCP_SAFETY_MODE", " SAFE_MANAGE ")
    load_settings.cache_clear()
    assert load_settings().safety_mode == "safe_manage"

    monkeypatch.setenv("APPLE_SHORTCUTS_MCP_SAFETY_MODE", "readonly")
    load_settings.cache_clear()
    assert load_settings().safety_mode == "safe_readonly"


def teardown_function() -> None:
    load_settings.cache_clear()
