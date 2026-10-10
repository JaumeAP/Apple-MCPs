import subprocess
from pathlib import Path

import pytest

from apple_reminders_mcp import reminders_bridge
from apple_reminders_mcp.reminders_bridge import RemindersBridge, RemindersBridgeError


def test_run_helper_maps_timeout(monkeypatch) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))
    monkeypatch.setattr(bridge, "_ensure_helper", lambda: None)

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == bridge._HELPER_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(reminders_bridge.subprocess, "run", fake_run)

    with pytest.raises(RemindersBridgeError) as excinfo:
        bridge.list_lists()
    assert excinfo.value.error_code == "HELPER_TIMEOUT"


def test_ensure_helper_compiles_to_temp_file_then_replaces(monkeypatch, tmp_path) -> None:
    source = tmp_path / "bridge.swift"
    source.write_text("// source")
    binary = tmp_path / "bin" / "helper"
    bridge = RemindersBridge(source, binary)
    outputs: list[Path] = []

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == bridge._COMPILE_TIMEOUT_SECONDS
        output = Path(command[command.index("-o") + 1])
        outputs.append(output)
        assert not binary.exists()
        output.write_text("compiled")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(reminders_bridge.subprocess, "run", fake_run)
    bridge._ensure_helper()

    assert outputs[0] != binary
    assert outputs[0].parent == binary.parent
    assert binary.read_text() == "compiled"
    assert not outputs[0].exists()


@pytest.mark.parametrize("deleted", [True, False])
def test_delete_list_maps_swift_object_id(monkeypatch, deleted) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))

    def fake_run_helper(command: str, *args: str) -> dict[str, object]:
        assert command == "delete-reminder-list"
        assert args == ("list-qa",)
        return {"deleted": deleted, "object_id": "list-qa"}

    monkeypatch.setattr(bridge, "_run_helper", fake_run_helper)
    result = bridge.delete_list("list-qa")
    assert result.ok is True
    assert result.list_id == "list-qa"
    assert result.deleted is deleted


def test_list_lists_normalizes_payload(monkeypatch) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))

    def fake_run_helper(command: str, *args: str) -> dict[str, object]:
        assert command == "list-reminder-lists"
        return {
            "items": [
                {
                    "list_id": "list-1",
                    "title": "Chores",
                    "source_title": "iCloud",
                    "allows_content_modifications": True,
                    "color_hex": "#D9A69F",
                }
            ]
        }

    monkeypatch.setattr(bridge, "_run_helper", fake_run_helper)
    lists = bridge.list_lists()

    assert len(lists) == 1
    assert lists[0].title == "Chores"


def test_get_reminder_maps_not_found(monkeypatch) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))

    def fake_run_helper(command: str, *args: str) -> dict[str, object]:
        raise RemindersBridgeError("REMINDER_NOT_FOUND", "missing")

    monkeypatch.setattr(bridge, "_run_helper", fake_run_helper)

    try:
        bridge.get_reminder("x-apple-reminder://missing")
    except RemindersBridgeError as exc:
        assert exc.error_code == "REMINDER_NOT_FOUND"
    else:
        raise AssertionError("Expected RemindersBridgeError")


def test_create_reminder_rejects_subtask_parent(monkeypatch) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))

    try:
        bridge.create_reminder(title="Child", list_id="list-1", parent_reminder_id="x-apple-reminder://parent")
    except RemindersBridgeError as exc:
        assert exc.error_code == "SUBTASKS_UNSUPPORTED"
    else:
        raise AssertionError("Expected RemindersBridgeError")
