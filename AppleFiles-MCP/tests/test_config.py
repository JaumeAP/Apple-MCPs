from pathlib import Path

import pytest

from apple_files_mcp.config import _default_roots, _parse_roots


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
