from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from apple_shortcuts_mcp.config import load_settings
from apple_shortcuts_mcp.models import ShortcutArtifact, ShortcutFolderInfo, ShortcutInfo, ShortcutRunResponse

UUID_PATTERN = re.compile(r"^(?P<name>.+?)\s+\((?P<identifier>[0-9A-Fa-f-]{36})\)$")


class ShortcutsBridgeError(Exception):
    def __init__(self, error_code: str, message: str, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.suggestion = suggestion


@dataclass(frozen=True)
class ShortcutCLIResult:
    returncode: int
    stdout: str
    stderr: str


class ShortcutsBridge:
    def __init__(self, shortcuts_command: str | None = None, timeout_seconds: int | None = None) -> None:
        settings = load_settings()
        self.shortcuts_command = shortcuts_command or settings.shortcuts_command
        self.timeout_seconds = timeout_seconds or settings.command_timeout_seconds
        self.allowed_roots = tuple(Path(os.path.realpath(root.expanduser())) for root in settings.allowed_roots)

    def _confine_path(self, path: str, *, is_output: bool = False) -> str:
        """Resolve a caller path with realpath and refuse it unless it sits inside an allowed root.

        Hidden segments are refused even inside a root. ~/Library is refused unless the path sits
        under an allowed root inside ~/Library (iCloud Drive by default, or an app folder you configure).
        The Library checks ignore case: APFS is case-insensitive by default and realpath keeps the typed case.
        ponytail: checked before the CLI runs, so a symlink swapped in afterwards is not caught.
        """
        resolved = Path(os.path.realpath(Path(path).expanduser()))
        folded = Path(str(resolved).lower())
        library = Path(os.path.realpath(Path.home() / "Library").lower())
        library_roots = [Path(str(root).lower()) for root in self.allowed_roots]
        library_roots = [root for root in library_roots if root.is_relative_to(library) and root != library]
        if (
            any(part.startswith(".") for part in resolved.parts)
            or (folded.is_relative_to(library) and not any(folded.is_relative_to(root) for root in library_roots))
            or not any(resolved.is_relative_to(root) for root in self.allowed_roots)
        ):
            raise ShortcutsBridgeError(
                "PATH_NOT_ALLOWED",
                f"Path is outside the allowed roots: {resolved}",
                "Use a path under Desktop, Documents, Downloads, iCloud Drive or the temp folder, "
                "or set APPLE_SHORTCUTS_MCP_ALLOWED_ROOTS.",
            )
        if is_output and resolved.exists():
            raise ShortcutsBridgeError(
                "OUTPUT_PATH_EXISTS",
                f"Output path already exists: {resolved}",
                "Choose an output path that does not exist yet.",
            )
        return str(resolved)

    def cli_available(self) -> bool:
        return shutil.which(self.shortcuts_command) is not None

    def list_folders(self) -> list[ShortcutFolderInfo]:
        output = self._run_cli(["list", "--folders"])
        folders = [line.strip() for line in self._split_lines(output.stdout)]
        items = [ShortcutFolderInfo(folder_name=folder, shortcut_count=len(self.list_shortcuts(folder_name=folder))) for folder in folders]
        return items

    def list_shortcuts(self, folder_name: str | None = None) -> list[ShortcutInfo]:
        args = ["list", "--show-identifiers"]
        if folder_name is not None:
            # The --opt=value form keeps a value starting with "-" from being read as an option.
            args.append(f"--folder-name={folder_name}")
        output = self._run_cli(args)
        items: list[ShortcutInfo] = []
        for line in self._split_lines(output.stdout):
            parsed = self._parse_shortcut_line(line)
            if parsed is not None:
                items.append(parsed)
        return items

    def view_shortcut(self, shortcut_name_or_identifier: str) -> ShortcutInfo:
        shortcut = self.resolve_shortcut(shortcut_name_or_identifier)
        output = self._run_cli(["view", "--", shortcut.name])
        if output.returncode != 0:
            raise self._map_error("view", output)
        return shortcut

    def run_shortcut(
        self,
        shortcut_name_or_identifier: str,
        input_paths: list[str] | None = None,
        output_path: str | None = None,
        output_type: str | None = None,
        input_text: str | None = None,
    ) -> ShortcutRunResponse:
        input_paths = [self._confine_path(path) for path in input_paths or []]
        if output_path is not None:
            output_path = self._confine_path(output_path, is_output=True)
        shortcut = self.resolve_shortcut(shortcut_name_or_identifier)
        args = ["run"]
        for path in input_paths:
            args.extend(["--input-path", path])
        if output_path is not None:
            args.extend(["--output-path", output_path])
        if output_type is not None:
            args.append(f"--output-type={output_type}")
        # "--" ends option parsing, so a shortcut name starting with "-" stays positional.
        args.extend(["--", shortcut.identifier or shortcut.name])

        output = self._run_cli(args, input_data=input_text)
        if output.returncode != 0:
            raise self._map_error("run", output)

        artifacts: list[ShortcutArtifact] = []
        if output_path is not None:
            artifact_path = Path(output_path)
            artifacts.append(
                ShortcutArtifact(
                    path=str(artifact_path),
                    exists=artifact_path.exists(),
                    kind="file",
                    size_bytes=artifact_path.stat().st_size if artifact_path.exists() else None,
                )
            )
        return ShortcutRunResponse(
            shortcut_name=shortcut.name,
            shortcut_identifier=shortcut.identifier,
            exit_code=output.returncode,
            stdout=output.stdout.rstrip(),
            stderr=output.stderr.rstrip(),
            artifacts=artifacts,
        )

    def resolve_shortcut(self, shortcut_name_or_identifier: str) -> ShortcutInfo:
        if self._looks_like_identifier(shortcut_name_or_identifier):
            matching = [item for item in self.list_shortcuts() if item.identifier == shortcut_name_or_identifier]
            if not matching:
                raise ShortcutsBridgeError(
                    "SHORTCUT_NOT_FOUND",
                    f"Shortcut '{shortcut_name_or_identifier}' was not found.",
                    "Run shortcuts_list_shortcuts to discover valid shortcut identifiers.",
                )
            return matching[0]

        matching = [item for item in self.list_shortcuts() if item.name == shortcut_name_or_identifier]
        if len(matching) == 1:
            return matching[0]
        if len(matching) > 1:
            raise ShortcutsBridgeError(
                "SHORTCUT_AMBIGUOUS",
                f"Shortcut name '{shortcut_name_or_identifier}' matched multiple shortcuts.",
                "Pass a shortcut identifier instead of a duplicated name.",
            )

        case_insensitive = [item for item in self.list_shortcuts() if item.name.lower() == shortcut_name_or_identifier.lower()]
        if len(case_insensitive) == 1:
            return case_insensitive[0]
        if len(case_insensitive) > 1:
            raise ShortcutsBridgeError(
                "SHORTCUT_AMBIGUOUS",
                f"Shortcut name '{shortcut_name_or_identifier}' matched multiple shortcuts.",
                "Pass a shortcut identifier instead of a duplicated name.",
            )

        raise ShortcutsBridgeError(
            "SHORTCUT_NOT_FOUND",
            f"Shortcut '{shortcut_name_or_identifier}' was not found.",
            "Run shortcuts_list_shortcuts to discover valid shortcut names and identifiers.",
        )

    def shortcuts_snapshot(self) -> dict[str, object]:
        folders = self.list_folders()
        shortcuts = self.list_shortcuts()
        return {
            "folders": [folder.model_dump() for folder in folders],
            "shortcuts": [shortcut.model_dump() for shortcut in shortcuts],
            "count": len(shortcuts),
        }

    def shortcuts_folder_snapshot(self, folder_name: str) -> dict[str, object]:
        shortcuts = self.list_shortcuts(folder_name=folder_name)
        return {
            "folder_name": folder_name,
            "shortcuts": [shortcut.model_dump() for shortcut in shortcuts],
            "count": len(shortcuts),
        }

    def _run_cli(self, args: list[str], input_data: str | None = None) -> ShortcutCLIResult:
        if not self.cli_available():
            raise ShortcutsBridgeError(
                "SHORTCUTS_CLI_NOT_FOUND",
                f"Command '{self.shortcuts_command}' was not found.",
                "Install or expose the official shortcuts CLI in PATH.",
            )

        try:
            completed = subprocess.run(
                [self.shortcuts_command, *args],
                input=input_data,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ShortcutsBridgeError(
                "CLI_TIMEOUT",
                f"Command '{self.shortcuts_command} {' '.join(args)}' timed out.",
                "Increase the timeout or simplify the shortcut.",
            ) from exc
        except OSError as exc:
            raise ShortcutsBridgeError(
                "SHORTCUTS_CLI_NOT_FOUND",
                f"Could not run '{self.shortcuts_command}': {exc}.",
                "This server requires macOS with the shortcuts CLI available.",
            ) from exc

        result = ShortcutCLIResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
        if result.returncode != 0:
            raise self._map_error(args[0], result)
        return result

    def _map_error(self, command: str, output: ShortcutCLIResult) -> ShortcutsBridgeError:
        text = (output.stderr or output.stdout or f"shortcuts {command} failed.").strip()
        lowered = text.lower()

        if "could not be processed" in lowered or "input" in lowered:
            return ShortcutsBridgeError(
                "SHORTCUT_INPUT_FAILED",
                text,
                "Check the shortcut's input types or provide a compatible input path.",
            )
        if "could not be found" in lowered or "not found" in lowered:
            return ShortcutsBridgeError(
                "SHORTCUT_NOT_FOUND",
                text,
                "Run shortcuts_list_shortcuts to discover valid names and identifiers.",
            )
        if "automation" in lowered or "permission" in lowered:
            return ShortcutsBridgeError(
                "PERMISSION_DENIED",
                text,
                "Allow automation access for the process that runs this MCP server.",
            )
        return ShortcutsBridgeError(
            "SHORTCUT_RUN_FAILED",
            text,
            "Inspect the shortcut in the Shortcuts app and retry.",
        )

    def _parse_shortcut_line(self, line: str) -> ShortcutInfo | None:
        text = line.strip()
        if not text:
            return None
        match = UUID_PATTERN.match(text)
        if match is not None:
            return ShortcutInfo(name=match.group("name"), identifier=match.group("identifier"))
        return ShortcutInfo(name=text)

    def _split_lines(self, raw: str) -> list[str]:
        return [line for line in raw.splitlines() if line.strip()]

    def _looks_like_identifier(self, value: str) -> bool:
        return bool(UUID_PATTERN.match(f"Shortcut ({value})"))
