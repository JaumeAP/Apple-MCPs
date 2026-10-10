from apple_shortcuts_mcp.shortcuts_bridge import ShortcutsBridge, ShortcutsBridgeError


class Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_parse_shortcuts_and_folders(monkeypatch) -> None:
    bridge = ShortcutsBridge(shortcuts_command="shortcuts", timeout_seconds=5)

    def fake_run_cli(args):
        key = tuple(args)
        if key == ("list", "--show-identifiers"):
            return Completed(
                0,
                "Open Trunk (9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D)\nStart Car (29B15189-C4FC-4823-89AE-45617D461CBA)\n",
            )
        if key == ("list", "--folders"):
            return Completed(0, "Home\nCar\n")
        if key == ("list", "--show-identifiers", "--folder-name", "Home"):
            return Completed(0, "Open Trunk (9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D)\n")
        if key == ("list", "--show-identifiers", "--folder-name", "Car"):
            return Completed(0, "Start Car (29B15189-C4FC-4823-89AE-45617D461CBA)\n")
        raise AssertionError(f"Unexpected args: {args}")

    monkeypatch.setattr(bridge, "_run_cli", fake_run_cli)

    shortcuts = bridge.list_shortcuts()
    folders = bridge.list_folders()

    assert shortcuts[0].name == "Open Trunk"
    assert shortcuts[0].identifier == "9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D"
    assert folders[0].folder_name == "Home"
    assert folders[0].shortcut_count == 1


def test_run_shortcut_maps_failure(monkeypatch, tmp_path) -> None:
    bridge = ShortcutsBridge(shortcuts_command="shortcuts", timeout_seconds=5)
    input_file = tmp_path / "input.txt"
    input_file.write_text("x")

    monkeypatch.setattr(
        bridge,
        "resolve_shortcut",
        lambda value: __import__("apple_shortcuts_mcp.models", fromlist=["ShortcutInfo"]).ShortcutInfo(
            name=value, identifier="9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D"
        ),
    )
    monkeypatch.setattr(bridge, "_run_cli", lambda args, input_data=None: Completed(1, "", "Error: The input of the shortcut could not be processed."))

    try:
        bridge.run_shortcut("Open Trunk", input_paths=[str(input_file)])
    except ShortcutsBridgeError as exc:
        assert exc.error_code == "SHORTCUT_INPUT_FAILED"
    else:
        raise AssertionError("Expected ShortcutsBridgeError")


def test_run_shortcut_passes_input_text(monkeypatch) -> None:
    bridge = ShortcutsBridge(shortcuts_command="shortcuts", timeout_seconds=5)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bridge,
        "resolve_shortcut",
        lambda value: __import__("apple_shortcuts_mcp.models", fromlist=["ShortcutInfo"]).ShortcutInfo(
            name=value, identifier="9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D"
        ),
    )

    def fake_run_cli(args, input_data=None):
        captured["args"] = args
        captured["input_data"] = input_data
        return Completed(0, "done", "")

    monkeypatch.setattr(bridge, "_run_cli", fake_run_cli)

    result = bridge.run_shortcut("Open Trunk", input_text="hello from stdin")

    assert result.exit_code == 0
    assert captured["args"] == ["run", "--", "9A2D4AFE-D4B5-418E-814A-DB97BAB3BE4D"]
    assert captured["input_data"] == "hello from stdin"


def test_run_shortcut_confines_paths(monkeypatch, tmp_path) -> None:
    bridge = ShortcutsBridge(shortcuts_command="shortcuts", timeout_seconds=5)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        bridge,
        "resolve_shortcut",
        lambda value: __import__("apple_shortcuts_mcp.models", fromlist=["ShortcutInfo"]).ShortcutInfo(name=value),
    )

    def fake_run_cli(args, input_data=None):
        captured["args"] = args
        return Completed(0, "", "")

    monkeypatch.setattr(bridge, "_run_cli", fake_run_cli)
    existing = tmp_path / "existing.txt"
    existing.write_text("keep")
    escape = tmp_path / "escape"
    escape.symlink_to("/etc")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()

    refused = [
        {"input_paths": ["/etc/hosts"]},
        {"input_paths": [str(escape / "hosts")]},
        {"input_paths": [str(hidden / "file.txt")]},
        {"input_paths": ["~/Library/Preferences/x.plist"]},
        {"output_path": "~/.zshrc"},
        {"output_path": str(existing)},
    ]
    for kwargs in refused:
        try:
            bridge.run_shortcut("-x", **kwargs)
        except ShortcutsBridgeError as exc:
            assert exc.error_code in {"PATH_NOT_ALLOWED", "OUTPUT_PATH_EXISTS"}
        else:
            raise AssertionError(f"Expected refusal for {kwargs}")
    assert "args" not in captured

    output = tmp_path / "out.txt"
    bridge.run_shortcut("-x", input_paths=[str(existing)], output_path=str(output))
    assert captured["args"][-2:] == ["--", "-x"]
    assert str(output.resolve()) in captured["args"]
