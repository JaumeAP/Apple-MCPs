import os
import subprocess
from pathlib import Path

import pytest

from apple_files_mcp.files_bridge import FilesBridge, FilesBridgeError


def test_recent_files_skips_timed_out_roots(monkeypatch, tmp_path) -> None:
    docs_root = tmp_path / "Documents"
    docs_root.mkdir()
    file_path = docs_root / "a.txt"
    file_path.write_text("hello")

    bridge = FilesBridge((Path("/Users/test/Downloads"), docs_root))

    def fake_run(command, capture_output, text, check, timeout):
        assert command[0] == "find"
        root = command[1]
        if root == "/Users/test/Downloads":
            raise subprocess.TimeoutExpired(command, timeout)

        class Completed:
            stdout = f"{file_path}\0"
            stderr = ""

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    entries = bridge.recent_files(limit=5)

    assert len(entries) == 1
    assert entries[0].path == str(file_path)


def test_move_path_refuses_existing_destination(tmp_path) -> None:
    source = tmp_path / "a.txt"
    destination = tmp_path / "b.txt"
    source.write_text("new")
    destination.write_text("keep")
    bridge = FilesBridge((tmp_path,))

    with pytest.raises(FilesBridgeError) as excinfo:
        bridge.move_path(str(source), str(destination))

    assert excinfo.value.error_code == "DESTINATION_EXISTS"
    assert destination.read_text() == "keep"
    assert source.exists()


def test_copy_path_copies_binary_and_keeps_source(tmp_path) -> None:
    source = tmp_path / "invoice.doc"
    payload = bytes(range(256)) * 64  # every byte value, as in an OLE .doc
    source.write_bytes(payload)
    destination = tmp_path / "copy" / "invoice.doc"
    destination.parent.mkdir()
    bridge = FilesBridge((tmp_path,))

    original, copied = bridge.copy_path(str(source), str(destination))

    assert (original, copied) == (str(source.resolve()), str(destination.resolve()))
    assert destination.read_bytes() == payload
    assert source.read_bytes() == payload
    assert destination.stat().st_mtime == source.stat().st_mtime


def test_copy_path_refuses_existing_destination_folder_and_outside_root(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "a.txt"
    source.write_text("new")
    existing = root / "b.txt"
    existing.write_text("keep")
    bridge = FilesBridge((root,))

    with pytest.raises(FilesBridgeError) as excinfo:
        bridge.copy_path(str(source), str(existing))
    assert excinfo.value.error_code == "DESTINATION_EXISTS"
    assert existing.read_text() == "keep"

    with pytest.raises(FilesBridgeError) as excinfo:
        bridge.copy_path(str(root), str(root / "folder-copy"))
    assert excinfo.value.error_code == "NOT_A_FILE"

    with pytest.raises(FilesBridgeError) as excinfo:
        bridge.copy_path(str(source), str(tmp_path / "outside.txt"))
    assert excinfo.value.error_code == "PATH_NOT_ALLOWED"
    assert not (tmp_path / "outside.txt").exists()


def test_recent_files_ignores_injected_paths_outside_roots(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.keychain-db"
    outside.write_text("secret")
    # A name holding newline + tab + an outside path, as in the old line-parsing attack.
    tricky = root / "x\n0\t" / str(outside).lstrip("/")
    tricky.parent.mkdir(parents=True)
    tricky.write_text("bait")
    bridge = FilesBridge((root,))

    paths = [entry.path for entry in bridge.recent_files(limit=10)]

    assert paths == [str(tricky)]


def test_open_path_refuses_executables_and_uses_separator(monkeypatch, tmp_path) -> None:
    script = tmp_path / "run.command"
    script.write_text("#!/bin/sh\n")
    plain_exec = tmp_path / "tool"
    plain_exec.write_text("#!/bin/sh\n")
    plain_exec.chmod(0o755)
    document = tmp_path / "notes.txt"
    document.write_text("hello")
    calls: list[list[str]] = []

    def fake_run(command, capture_output, check, text, timeout):
        calls.append(command)

        class Completed:
            stdout = ""

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = FilesBridge((tmp_path,))

    for unsafe in (script, plain_exec):
        with pytest.raises(FilesBridgeError) as excinfo:
            bridge.open_path(str(unsafe))
        assert excinfo.value.error_code == "UNSAFE_OPEN_TARGET"
    bridge.open_path(str(document))

    assert calls == [["open", "--", str(document.resolve())]]


def test_open_path_location_docs_executable_documents_and_unreadable(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_run(command, capture_output, check, text, timeout):
        calls.append(command)

        class Completed:
            stdout = ""

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    bridge = FilesBridge((tmp_path,))
    for name in ("app.jar", "server.afploc", "share.smbloc", "host.vncloc", "site.ftploc"):
        (tmp_path / name).write_text("x")
        with pytest.raises(FilesBridgeError) as excinfo:
            bridge.open_path(str(tmp_path / name))
        assert excinfo.value.error_code == "UNSAFE_OPEN_TARGET"
    script = tmp_path / "job.py"
    script.write_text("print(1)")
    script.chmod(0o755)
    with pytest.raises(FilesBridgeError):
        bridge.open_path(str(script))
    # A photo copied from exFAT keeps -rwxrwxrwx and must still open.
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    photo.chmod(0o777)
    bridge.open_path(str(photo))
    assert calls == [["open", "--", str(photo.resolve())]]
    locked = tmp_path / "locked.pdf"
    locked.write_bytes(b"%PDF")
    locked.chmod(0o000)
    try:
        with pytest.raises(FilesBridgeError) as excinfo:
            bridge.open_path(str(locked))
        assert excinfo.value.error_code == "READ_FAILED"
    finally:
        locked.chmod(0o644)


def test_read_text_file_bounds_read_and_rejects_non_regular(tmp_path) -> None:
    text_file = tmp_path / "a.txt"
    text_file.write_text("abcdef")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    bridge = FilesBridge((tmp_path,))

    assert bridge.read_text_file(str(text_file), max_bytes=-1) == ("a", True)
    assert bridge.read_text_file(str(text_file), max_bytes=10**12) == ("abcdef", False)
    with pytest.raises(FilesBridgeError) as excinfo:
        bridge.read_text_file(str(fifo))
    assert excinfo.value.error_code == "NOT_A_FILE"
