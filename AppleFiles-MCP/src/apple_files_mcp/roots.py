"""List, add or remove the folders Apple Files may touch (the roots file).

Run it outside the Claude Code sandbox: the roots file lives in ~/.config, which the
sandbox cannot write. The server reads the file at startup, so restart it to apply.
"""

import argparse
import os
import sys
from pathlib import Path

from apple_files_mcp.config import roots_file


def _entry(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _absolute(raw: str) -> str:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{raw!r} is not an absolute path; use an absolute path or ~/.")
    return str(path)


def _write(lines: list[str]) -> None:
    target = roots_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(target.name + ".new")
    scratch.write_text("".join(f"{line}\n" for line in lines))
    os.replace(scratch, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apple-files-roots", description="Manage the Apple Files roots file.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show the allowed folders")
    commands.add_parser("add", help="allow a folder").add_argument("path")
    commands.add_parser("remove", help="stop allowing a folder").add_argument("path")
    args = parser.parse_args(argv)

    path = roots_file()
    lines = path.read_text().splitlines() if path.is_file() else []
    if args.command == "list":
        print(f"# {path}" + ("" if path.is_file() else " (missing: the environment variable applies)"))
        print("\n".join(entry for entry in map(_entry, lines) if entry))
        return 0

    target = _absolute(args.path)
    present = [_absolute(entry) for entry in map(_entry, lines) if entry]
    if args.command == "add":
        if target not in present:
            _write([*lines, target])
    else:
        if target not in present:
            print(f"{target} is not in the list.", file=sys.stderr)
            return 1
        _write([line for line in lines if not (_entry(line) and _absolute(_entry(line)) == target)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
