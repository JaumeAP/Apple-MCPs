from pathlib import Path

import pytest

from apple_files_mcp.config import _default_roots, _parse_roots, _roots_value, roots_file
from apple_files_mcp.roots import main as roots_main


def test_unset_or_empty_value_keeps_the_default_roots() -> None:
    assert _parse_roots(None) == _default_roots()
    assert _parse_roots("") == _default_roots()


@pytest.mark.parametrize("value", [",", " ", " , ", ",,"])
def test_blank_value_fails_closed_with_no_roots(value) -> None:
    assert _parse_roots(value) == ()


def test_entries_are_trimmed_and_expanded() -> None:
    roots = _parse_roots(" /tmp/a , ~/b ,")

    assert roots == (Path("/tmp/a"), Path.home() / "b")


@pytest.mark.parametrize("value", ["relative/dir", "/tmp/a,docs", "."])
def test_relative_entries_are_rejected(value) -> None:
    with pytest.raises(ValueError, match="absolute"):
        _parse_roots(value)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _write_roots(home: Path, text: str) -> None:
    path = roots_file()
    path.parent.mkdir(parents=True)
    path.write_text(text)


def test_without_a_roots_file_the_environment_value_is_used(home) -> None:
    assert _roots_value("/tmp/a") == "/tmp/a"
    assert _roots_value(None) is None


def test_the_roots_file_wins_over_the_environment_and_skips_comments(home) -> None:
    _write_roots(home, "# invoices\n/tmp/a\n\n/tmp/b  # work\n")

    assert _parse_roots(_roots_value("/tmp/ignored")) == (Path("/tmp/a"), Path("/tmp/b"))


def test_an_empty_roots_file_fails_closed(home) -> None:
    _write_roots(home, "# nothing allowed\n")

    assert _parse_roots(_roots_value("/tmp/ignored")) == ()


def test_the_roots_command_adds_lists_and_removes(home, capsys) -> None:
    assert roots_main(["add", "/tmp/a"]) == 0
    assert roots_main(["add", "/tmp/a"]) == 0
    assert roots_main(["add", "/tmp/b"]) == 0
    assert roots_file().read_text() == "/tmp/a\n/tmp/b\n"

    assert roots_main(["remove", "/tmp/a"]) == 0
    assert roots_file().read_text() == "/tmp/b\n"
    assert roots_main(["remove", "/tmp/a"]) == 1

    capsys.readouterr()
    assert roots_main(["list"]) == 0
    assert "/tmp/b" in capsys.readouterr().out


def test_the_roots_command_rejects_a_relative_path(home) -> None:
    with pytest.raises(SystemExit, match="absolute"):
        roots_main(["add", "docs"])
