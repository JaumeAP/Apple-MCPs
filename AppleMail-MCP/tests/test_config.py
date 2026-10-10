import os

import pytest

from apple_mail_mcp.config import load_settings
from apple_mail_mcp.models import SafetyProfile


def test_load_settings_defaults_invalid_transport_to_stdio() -> None:
    previous_transport = os.environ.get("APPLE_MAIL_MCP_TRANSPORT")
    os.environ["APPLE_MAIL_MCP_TRANSPORT"] = "invalid"

    try:
        settings = load_settings()
    finally:
        if previous_transport is None:
            os.environ.pop("APPLE_MAIL_MCP_TRANSPORT", None)
        else:
            os.environ["APPLE_MAIL_MCP_TRANSPORT"] = previous_transport

    assert settings.transport == "stdio"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, SafetyProfile.SAFE_MANAGE), (" Full_Access ", SafetyProfile.FULL_ACCESS), ("full-access", SafetyProfile.SAFE_READONLY)],
)
def test_load_settings_normalizes_safety_profile_and_fails_closed(monkeypatch, raw, expected) -> None:
    if raw is None:
        monkeypatch.delenv("APPLE_MAIL_MCP_SAFETY_PROFILE", raising=False)
    else:
        monkeypatch.setenv("APPLE_MAIL_MCP_SAFETY_PROFILE", raw)

    assert load_settings().safety_profile is expected
