from apple_messages_mcp.config import load_settings
from apple_messages_mcp.permissions import SafetyError, ensure_action_allowed


def test_safe_readonly_blocks_send(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_MESSAGES_MCP_SAFETY_MODE", "safe_readonly")
    load_settings.cache_clear()

    try:
        ensure_action_allowed("messages_send_message")
    except SafetyError as exc:
        assert exc.error_code == "WRITE_BLOCKED"
    else:
        raise AssertionError("Expected SafetyError")


def test_safety_mode_is_normalized_and_fails_closed(monkeypatch) -> None:
    for raw, expected in (("SAFE_READONLY", "safe_readonly"), (" Safe_Manage ", "safe_manage"), ("readonly", "safe_readonly"), ("bogus", "safe_readonly")):
        monkeypatch.setenv("APPLE_MESSAGES_MCP_SAFETY_MODE", raw)
        load_settings.cache_clear()
        assert load_settings().safety_mode == expected

    monkeypatch.delenv("APPLE_MESSAGES_MCP_SAFETY_MODE")
    load_settings.cache_clear()
    assert load_settings().safety_mode == "full_access"


def teardown_function() -> None:
    load_settings.cache_clear()
