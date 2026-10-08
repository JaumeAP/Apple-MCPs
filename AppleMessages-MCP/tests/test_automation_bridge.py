import subprocess

import pytest

from apple_messages_mcp import messages_automation_bridge as bridge_module
from apple_messages_mcp.messages_automation_bridge import MessagesAutomationBridge, MessagesAutomationBridgeError


def _capture_run(monkeypatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    return calls


def test_send_message_passes_values_as_argv_not_script(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    recipient = 'evil" & (do shell script "id") & "'
    text = 'hi" \\ end tell'

    MessagesAutomationBridge().send_message(recipient, text)

    command = calls[0]["command"]
    script = command[2]
    assert command[:2] == ["osascript", "-e"]
    assert command[3:] == ["--", recipient, text]
    assert recipient not in script and text not in script
    assert "on run argv" in script
    assert calls[0]["timeout"] == 30


def test_send_to_group_passes_values_as_argv(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    MessagesAutomationBridge().send_to_group("chat-guid", "hello there")

    command = calls[0]["command"]
    assert command[3:] == ["--", "chat-guid", "hello there"]
    assert "hello there" not in command[2]


def test_send_attachment_passes_values_as_argv(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    MessagesAutomationBridge().send_attachment("+15551234567", "/Users/x/Downloads/a.png", "caption")
    MessagesAutomationBridge().send_attachment("+15551234567", "/Users/x/Downloads/a.png", "   ")

    assert calls[0]["command"][3:] == ["--", "+15551234567", "/Users/x/Downloads/a.png", "caption"]
    assert calls[1]["command"][3:] == ["--", "+15551234567", "/Users/x/Downloads/a.png", ""]
    assert "a.png" not in calls[0]["command"][2]


def test_timeout_maps_to_bridge_error(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    with pytest.raises(MessagesAutomationBridgeError) as excinfo:
        MessagesAutomationBridge().send_message("+15551234567", "hello")

    assert excinfo.value.error_code == "AUTOMATION_TIMEOUT"
    assert "may or may not have been sent" in excinfo.value.message
    assert "before retrying" in (excinfo.value.suggestion or "")
