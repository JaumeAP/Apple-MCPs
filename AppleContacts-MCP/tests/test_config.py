from pathlib import Path

from apple_contacts_mcp.config import load_settings


def test_load_settings_uses_packaged_helper_source(monkeypatch) -> None:
    monkeypatch.delenv("APPLE_CONTACTS_MCP_HELPER_BUILD_DIR", raising=False)
    load_settings.cache_clear()

    try:
        settings = load_settings()
    finally:
        load_settings.cache_clear()

    assert settings.helper_source.name == "contacts_bridge.swift"
    assert settings.helper_source.parent.name == "apple_contacts_mcp"
    assert settings.helper_source.exists()
    assert settings.helper_binary == Path.home() / ".apple-mcps" / "build" / "apple-contacts-bridge"
    assert settings.safety_mode == "safe_manage"


def test_load_settings_honours_helper_build_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APPLE_CONTACTS_MCP_HELPER_BUILD_DIR", str(tmp_path))
    load_settings.cache_clear()

    try:
        settings = load_settings()
    finally:
        load_settings.cache_clear()

    assert settings.helper_binary == tmp_path / "apple-contacts-bridge"
