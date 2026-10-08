from apple_system_mcp.system_bridge import SystemBridge, SystemBridgeError


def test_context_snapshot_degrades_when_frontmost_lookup_times_out(monkeypatch) -> None:
    bridge = SystemBridge()

    class Battery:
        def model_dump(self):
            return {"percentage": 88, "raw": "battery"}

    monkeypatch.setattr(bridge, "battery", lambda: Battery())
    monkeypatch.setattr(
        bridge,
        "frontmost_application",
        lambda: (_ for _ in ()).throw(SystemBridgeError("COMMAND_TIMEOUT", "timed out")),
    )
    monkeypatch.setattr(bridge, "running_apps", lambda: [])
    monkeypatch.setattr(
        bridge,
        "focus_status",
        lambda: {
            "focus_supported": False,
            "focus_active": None,
            "focus_name": None,
            "observed_at": "2026-04-07T16:00:00-05:00",
            "source": "unsupported_local_install",
            "confidence": 0.0,
            "notes": [],
        },
    )

    snapshot = bridge.context_snapshot()

    assert snapshot["battery"]["percentage"] == 88
    assert snapshot["frontmost_app"] is None
    assert any("Frontmost application unavailable" in note for note in snapshot["notification_history_notes"])


def test_show_notification_passes_untrusted_fields_as_osascript_arguments(monkeypatch) -> None:
    bridge = SystemBridge()
    captured: list[tuple[str, ...]] = []
    payload = '\\\"\ndo shell script "unexpected"\n--'
    monkeypatch.setattr(bridge, "_run", lambda *command: captured.append(command) or "")

    bridge.show_notification(title=payload, body=payload, subtitle=payload)

    command = captured[0]
    separator = command.index("--")
    script = command[:separator]
    assert payload not in script
    assert command[separator + 1 :] == (payload, payload, payload)


def test_show_notification_preserves_optional_subtitle(monkeypatch) -> None:
    bridge = SystemBridge()
    captured: list[tuple[str, ...]] = []
    monkeypatch.setattr(bridge, "_run", lambda *command: captured.append(command) or "")

    bridge.show_notification(title="Title", body="Body")

    assert captured[0][-4:] == ("--", "Body", "Title", "")


def ready_bridge(tmp_path):
    """A bridge whose apps helper binary is newer than its source, so nothing is compiled."""
    import os

    source = tmp_path / "system_apps_bridge.swift"
    binary = tmp_path / "apple-system-apps-bridge"
    source.touch()
    binary.touch()
    os.utime(binary, (source.stat().st_mtime + 1, source.stat().st_mtime + 1))
    return SystemBridge(source, binary)


def test_target_application_uses_native_helper_commands(monkeypatch, tmp_path) -> None:
    bridge = ready_bridge(tmp_path)
    calls = []

    def fake_run(*command, input_text=None):
        calls.append(command[1:])
        return '{"bundle_id":"com.apple.finder","name":"Finder","process_id":693}'

    monkeypatch.setattr(bridge, "_run", fake_run)

    assert bridge.frontmost_application().name == "Finder"
    assert bridge._target_application(bundle_id="com.apple.finder").process_id == 693
    assert bridge._target_application(application="Finder").bundle_id == "com.apple.finder"
    assert calls == [("frontmost",), ("bundle-id", "com.apple.finder"), ("name", "Finder")]


def test_running_apps_parses_helper_list(monkeypatch, tmp_path) -> None:
    bridge = ready_bridge(tmp_path)
    monkeypatch.setattr(
        bridge,
        "_run",
        lambda *command, input_text=None: '[{"bundle_id":"","name":"Tool","process_id":7},'
        '{"bundle_id":"com.apple.mail","name":"Mail","process_id":880}]',
    )

    apps = bridge.running_apps()

    assert [(app.name, app.bundle_id, app.process_id) for app in apps] == [
        ("Tool", None, 7),
        ("Mail", "com.apple.mail", 880),
    ]


def test_running_apps_rejects_invalid_helper_output(monkeypatch, tmp_path) -> None:
    bridge = ready_bridge(tmp_path)
    monkeypatch.setattr(bridge, "_run", lambda *command, input_text=None: "not json")

    try:
        bridge.running_apps()
    except SystemBridgeError as exc:
        assert exc.error_code == "INVALID_APP_RESPONSE"
    else:
        raise AssertionError("Expected SystemBridgeError")


def test_apps_helper_compiles_once_when_missing(monkeypatch, tmp_path) -> None:
    import subprocess
    from pathlib import Path

    from apple_system_mcp import system_bridge

    source = tmp_path / "system_apps_bridge.swift"
    source.touch()
    bridge = SystemBridge(source, tmp_path / "build" / "apple-system-apps-bridge")
    compiles = []

    def fake_compile(command, **kwargs):
        compiles.append(command)
        Path(command[-1]).touch()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(system_bridge.subprocess, "run", fake_compile)

    bridge._ensure_apps_helper()
    bridge._ensure_apps_helper()

    assert compiles == [["swiftc", "-O", str(source), "-o", str(bridge.apps_helper_binary)]]


def test_apps_helper_source_compiles() -> None:
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    import pytest

    if sys.platform != "darwin" or shutil.which("swiftc") is None:
        pytest.skip("swiftc is only available on macOS with the Xcode tools")
    source = Path(__file__).resolve().parents[1] / "src" / "apple_system_mcp" / "system_apps_bridge.swift"
    completed = subprocess.run(["swiftc", "-typecheck", str(source)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr or completed.stdout
