import subprocess
from pathlib import Path

import pytest

from apple_maps_mcp.maps_bridge import AppleMapsBridge, MapsBridgeError


def test_run_helper_maps_timeout_to_structured_error(monkeypatch) -> None:
    bridge = AppleMapsBridge(Path("/tmp/apple_maps_bridge.swift"), Path("/tmp/apple-maps-bridge"))

    monkeypatch.setattr(bridge, "_ensure_helper", lambda: None)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="apple-maps-bridge", timeout=20)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(MapsBridgeError) as exc_info:
        bridge.search_places("coffee")

    assert exc_info.value.error_code == "HELPER_TIMEOUT"


def test_ensure_helper_compiles_to_temp_file_and_replaces_atomically(monkeypatch, tmp_path) -> None:
    source = tmp_path / "apple_maps_bridge.swift"
    source.write_text("// swift")
    binary = tmp_path / "bin" / "apple-maps-bridge"
    bridge = AppleMapsBridge(source, binary)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        output = Path(cmd[cmd.index("-o") + 1])
        assert output != binary and output.parent == binary.parent
        assert not binary.exists()
        output.write_text("compiled")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    bridge._ensure_helper()

    assert binary.read_text() == "compiled"
    assert sorted(p.name for p in binary.parent.iterdir()) == ["apple-maps-bridge"]
    assert calls[0][1]["timeout"] == bridge.timeout_seconds
