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


def test_overlapping_compiles_use_distinct_temp_files(monkeypatch, tmp_path) -> None:
    source = tmp_path / "bridge.swift"
    source.write_text("// source")
    binary = tmp_path / "bin" / "helper"
    bridge = RemindersBridge(source, binary)
    outputs: list[Path] = []

    def fake_run(command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        outputs.append(output)
        output.write_text("compiled")
        if len(outputs) == 1:
            # A second thread compiles and installs while this compile is still running.
            bridge._ensure_helper()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(reminders_bridge.subprocess, "run", fake_run)
    bridge._ensure_helper()

    assert outputs[0] != outputs[1]
    assert binary.read_text() == "compiled"


def test_failed_helper_install_maps_to_bridge_error(monkeypatch, tmp_path) -> None:
    source = tmp_path / "bridge.swift"
    source.write_text("// source")
    bridge = RemindersBridge(source, tmp_path / "bin" / "helper")
    monkeypatch.setattr(reminders_bridge.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""))

    def vanished(src, dst):
        raise FileNotFoundError(src)

    monkeypatch.setattr(reminders_bridge.os, "replace", vanished)

    with pytest.raises(RemindersBridgeError) as excinfo:
        bridge._ensure_helper()
    assert excinfo.value.error_code == "HELPER_COMPILE_FAILED"


def test_mutation_timeout_does_not_suggest_a_blind_retry(monkeypatch) -> None:
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))
    monkeypatch.setattr(bridge, "_ensure_helper", lambda: None)

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(reminders_bridge.subprocess, "run", fake_run)

    with pytest.raises(RemindersBridgeError) as excinfo:
        bridge.delete_reminder("x-apple-reminder://r")
    assert excinfo.value.error_code == "HELPER_TIMEOUT"
    assert "may or may not have been applied" in excinfo.value.message
    assert "before retrying" in excinfo.value.suggestion


def test_update_reminder_sends_empty_notes_to_clear_them(monkeypatch) -> None:
    # The helper reads "" as "clear": the only way to remove notes, since None means no change.
    bridge = RemindersBridge(Path("/tmp/source.swift"), Path("/tmp/helper"))
    requests: list[tuple[str, ...]] = []

    def fake_run_helper(command: str, *args: str) -> dict[str, object]:
        requests.append((command, *args))
        return {}

    monkeypatch.setattr(bridge, "_run_helper", fake_run_helper)
    monkeypatch.setattr(reminders_bridge.ReminderDetail, "model_validate", staticmethod(lambda payload: payload))

    bridge.update_reminder("x-apple-reminder://r", title="New", notes="")
    bridge.update_reminder("x-apple-reminder://r", title="New")

    assert requests == [
        ("update-reminder", "x-apple-reminder://r", '{"title": "New", "notes": ""}'),
        ("update-reminder", "x-apple-reminder://r", '{"title": "New"}'),
    ]


def test_delete_reminder_list_refuses_mixed_event_calendars() -> None:
    # The Swift helper cannot run under pytest; pin the guard in its source.
    source = (Path(reminders_bridge.__file__).parent / "apple_pim_bridge.swift").read_text()

    assert "guard calendar.allowedEntityTypes.contains(.reminder), !calendar.allowedEntityTypes.contains(.event) else {" in source


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
