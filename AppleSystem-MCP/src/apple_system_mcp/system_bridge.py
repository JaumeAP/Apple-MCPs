from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from apple_system_mcp.config import load_settings
from apple_system_mcp.models import AppRecord, BatteryStatus

HELPER_COMPILE_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SystemBridgeError(Exception):
    error_code: str
    message: str
    suggestion: str | None = None


class SystemBridge:
    _SPECIAL_KEY_CODES: ClassVar[dict[str, int]] = {
        "return": 36,
        "enter": 76,
        "tab": 48,
        "space": 49,
        "escape": 53,
        "esc": 53,
        "delete": 51,
        "forward_delete": 117,
        "left": 123,
        "right": 124,
        "down": 125,
        "up": 126,
    }
    # Terminals and apps that run typed code (script editors, IDEs with an integrated
    # terminal). GUI input to them is refused even when they are allow-listed.
    _TERMINAL_APP_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "terminal",
            "iterm2",
            "iterm",
            "warp",
            "ghostty",
            "kitty",
            "alacritty",
            "wezterm",
            "script editor",
            "automator",
            "shortcuts",
            "visual studio code",
            "code",
            "cursor",
            "xcode",
            "hyper",
            "rio",
            "tabby",
            "zed",
        }
    )
    _TERMINAL_BUNDLE_IDS: ClassVar[frozenset[str]] = frozenset(
        {
            "com.apple.terminal",
            "com.googlecode.iterm2",
            "dev.warp.warp-stable",
            "dev.warp.warp",
            "com.mitchellh.ghostty",
            "net.kovidgoyal.kitty",
            "org.alacritty",
            "io.alacritty",
            "com.github.wez.wezterm",
            "com.apple.scripteditor2",
            "com.apple.automator",
            "com.apple.shortcuts",
            "com.microsoft.vscode",
            "com.microsoft.vscodeinsiders",
            "com.vscodium",
            "com.todesktop.230313mzl4w4u92",
            "com.apple.dt.xcode",
            "co.zeit.hyper",
            "com.raphaelamorim.rio",
            "org.tabby",
            "com.tabby",
            "dev.zed.zed",
        }
    )
    _BUNDLE_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
    _PREFERENCE_DOMAIN_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)+|[A-Za-z][A-Za-z0-9_-]*")
    # Brings the application whose bundle identifier is `targetBundle` to the front and
    # aborts unless it is frontmost, since System Events keystrokes and clicks go to the
    # frontmost application. A background-only app never comes to the front, so it is
    # refused here too.
    # ponytail: a window can still steal focus between this check and the input; no
    # unsigned API closes that race.
    _FRONTMOST_GUARD: ClassVar[list[str]] = [
        "tell application id targetBundle to activate",
        'tell application "System Events"',
        "repeat 20 times",
        "set frontBundle to bundle identifier of first application process whose frontmost is true",
        "if frontBundle is targetBundle then exit repeat",
        "delay 0.05",
        "end repeat",
        "end tell",
        'if frontBundle is not targetBundle then error "GUI_TARGET_NOT_FRONTMOST"',
    ]

    def __init__(self, apps_helper_source: Path | None = None, apps_helper_binary: Path | None = None) -> None:
        if apps_helper_source is None or apps_helper_binary is None:
            settings = load_settings()
            apps_helper_source = apps_helper_source or settings.apps_helper_source
            apps_helper_binary = apps_helper_binary or settings.apps_helper_binary
        self.apps_helper_source = apps_helper_source
        self.apps_helper_binary = apps_helper_binary

    def _run(self, *command: str, input_text: str | None = None) -> str:
        try:
            result = subprocess.run(
                list(command),
                input=input_text,
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise SystemBridgeError("COMMAND_NOT_FOUND", f"Missing command: {command[0]}", "Run this server on macOS.") from exc
        except OSError as exc:
            raise SystemBridgeError("COMMAND_FAILED", f"Could not run {command[0]}: {exc}", "Run this server on macOS.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() or exc.stdout.strip()
            stderr_lower = stderr.lower()
            if "not allowed assistive access" in stderr_lower or "access for assistive devices" in stderr_lower:
                raise SystemBridgeError(
                    "ACCESSIBILITY_PERMISSION_REQUIRED",
                    stderr or "Accessibility permission is required for this action.",
                    "Grant Accessibility access to the host app in System Settings and retry.",
                ) from exc
            if "not authorized to send apple events" in stderr_lower or "not permitted to send keystrokes" in stderr_lower:
                raise SystemBridgeError(
                    "AUTOMATION_PERMISSION_REQUIRED",
                    stderr or "Automation permission is required for this action.",
                    "Approve the macOS automation prompt for the host app and retry.",
                ) from exc
            if "GUI_TARGET_NOT_FRONTMOST" in stderr:
                raise SystemBridgeError(
                    "GUI_TARGET_NOT_FRONTMOST",
                    "The target application did not become frontmost, so no input was sent.",
                    "Bring the target application to the front (background-only apps cannot receive GUI input) and retry.",
                ) from exc
            raise SystemBridgeError("COMMAND_FAILED", stderr or "System command failed.", "Check macOS privacy permissions and retry.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemBridgeError("COMMAND_TIMEOUT", f"System command timed out: {command[0]}", "Retry the request.") from exc
        return result.stdout.strip()

    def _run_osascript(self, lines: list[str], args: list[str] | None = None) -> str:
        command = ["osascript"]
        for line in lines:
            command.extend(["-e", line])
        if args:
            command.append("--")
            command.extend(args)
        return self._run(*command)

    def _write_defaults(self, domain: str, key: str, value_flag: str, value: str, current_host: bool = False) -> None:
        command = ["defaults"]
        if current_host:
            command.append("-currentHost")
        command.extend(["write", domain, key, value_flag, value])
        self._run(*command)

    def _delete_default(self, domain: str, key: str, current_host: bool = False) -> None:
        command = ["defaults"]
        if current_host:
            command.append("-currentHost")
        command.extend(["delete", domain, key])
        try:
            self._run(*command)
        except SystemBridgeError as exc:
            if exc.error_code == "COMMAND_FAILED" and "does not exist" in exc.message.lower():
                return
            raise

    def _restart_process(self, process_name: str) -> None:
        try:
            subprocess.run(
                ["killall", process_name],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise SystemBridgeError("COMMAND_NOT_FOUND", "Missing killall command.", "Run this server on macOS.") from exc
        except OSError as exc:
            raise SystemBridgeError("COMMAND_FAILED", f"Could not run killall: {exc}", "Run this server on macOS.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() or exc.stdout.strip()
            if "no matching processes" in stderr.lower():
                return
            raise SystemBridgeError("COMMAND_FAILED", stderr or f"Could not restart {process_name}.", "Retry the request.") from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemBridgeError("COMMAND_TIMEOUT", f"Timed out while restarting {process_name}.", "Retry the request.") from exc

    def _ensure_apps_helper(self) -> Path:
        """Return the compiled apps helper for the current source, compiling it if needed.

        The binary name carries a hash of the source, so an existing binary is always
        current. Compilation writes to a per-process temporary file and renames it into
        place, so concurrent servers never run a half-written binary.
        """
        try:
            source_bytes = self.apps_helper_source.read_bytes()
        except OSError as exc:
            raise SystemBridgeError(
                "HELPER_SOURCE_MISSING",
                f"Missing native helper source at '{self.apps_helper_source}'.",
                "Restore system_apps_bridge.swift and retry.",
            ) from exc
        digest = hashlib.sha256(source_bytes).hexdigest()[:12]
        binary = self.apps_helper_binary.with_name(f"{self.apps_helper_binary.name}-{digest}")
        if binary.exists():
            return binary
        binary.parent.mkdir(parents=True, exist_ok=True)
        temporary = binary.with_name(f"{binary.name}.{os.getpid()}.tmp")
        try:
            try:
                completed = subprocess.run(
                    ["swiftc", "-O", str(self.apps_helper_source), "-o", str(temporary)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=HELPER_COMPILE_TIMEOUT_SECONDS,
                )
            except OSError as exc:
                raise SystemBridgeError(
                    "SWIFTC_UNAVAILABLE",
                    f"Could not run 'swiftc': {exc}.",
                    "This server requires macOS with the Swift toolchain (swiftc) available.",
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise SystemBridgeError(
                    "HELPER_COMPILE_FAILED",
                    f"Compiling the native helper exceeded the {HELPER_COMPILE_TIMEOUT_SECONDS}-second timeout.",
                    "Confirm Xcode command line tools and Swift are available, then retry.",
                ) from exc
            if completed.returncode != 0:
                raise SystemBridgeError(
                    "HELPER_COMPILE_FAILED",
                    completed.stderr.strip() or completed.stdout.strip() or "Failed to compile the native helper.",
                    "Confirm Xcode command line tools and Swift are available, then retry.",
                )
            os.replace(temporary, binary)
        finally:
            temporary.unlink(missing_ok=True)
        return binary

    def _run_apps_helper(self, *args: str) -> object:
        """Runs the NSWorkspace helper (system_apps_bridge.swift) and decodes its JSON."""
        raw = self._run(str(self._ensure_apps_helper()), *args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemBridgeError("INVALID_APP_RESPONSE", "Could not parse application identity.", "Retry the request.") from exc

    @staticmethod
    def _app_record(item: object) -> AppRecord:
        if not isinstance(item, dict):
            raise SystemBridgeError("INVALID_APP_RESPONSE", "Could not parse application identity.", "Retry the request.")
        process_id = item.get("process_id")
        return AppRecord(
            name=str(item.get("name", "")).strip(),
            bundle_id=str(item.get("bundle_id") or "").strip() or None,
            process_id=process_id if isinstance(process_id, int) and not isinstance(process_id, bool) else None,
        )

    def _target_application(self, application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        if bundle_id is not None and not bundle_id.strip():
            raise SystemBridgeError("INVALID_INPUT", "bundle_id must not be empty.", "Provide a valid application bundle identifier.")
        if application is not None and not application.strip():
            raise SystemBridgeError("INVALID_INPUT", "application must not be empty.", "Provide a valid application name.")
        if bundle_id:
            payload = self._run_apps_helper("bundle-id", bundle_id)
        elif application:
            payload = self._run_apps_helper("name", application)
        else:
            payload = self._run_apps_helper("frontmost")
        return self._app_record(payload)

    def _gui_input_target(self, application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        """Resolve the target of a GUI input action: a named, allow-listed, non-terminal application.

        Clicks and keystrokes are never sent to whatever happens to be frontmost, only to an
        application whose bundle id is in APPLE_SYSTEM_MCP_GUI_ALLOWED_APPS, and never to a
        terminal or another app that runs typed code, even when it is allow-listed.
        """
        if not (application and application.strip()) and not (bundle_id and bundle_id.strip()):
            raise SystemBridgeError(
                "INVALID_INPUT",
                "application or bundle_id is required for GUI input actions.",
                "Name the target application; GUI input is never sent to the frontmost app implicitly.",
            )
        requested_name = (application or "").strip().lower().removesuffix(".app")
        if requested_name in self._TERMINAL_APP_NAMES or (bundle_id or "").strip().lower() in self._TERMINAL_BUNDLE_IDS:
            raise SystemBridgeError("TERMINAL_TARGET_REFUSED", "GUI input to terminal applications is refused.", "Target a non-terminal application.")
        target_app = self._target_application(application=application, bundle_id=bundle_id)
        if target_app.name.strip().lower() in self._TERMINAL_APP_NAMES or (target_app.bundle_id or "").lower() in self._TERMINAL_BUNDLE_IDS:
            raise SystemBridgeError("TERMINAL_TARGET_REFUSED", "GUI input to terminal applications is refused.", "Target a non-terminal application.")
        if not target_app.bundle_id or target_app.bundle_id.lower() not in load_settings().gui_allowed_apps:
            raise SystemBridgeError(
                "GUI_TARGET_NOT_ALLOWED",
                f"GUI input to '{target_app.name}' ({target_app.bundle_id or 'no bundle id'}) is not allowed.",
                "Add the application's bundle id to APPLE_SYSTEM_MCP_GUI_ALLOWED_APPS (comma-separated) and restart the server.",
            )
        return target_app

    def battery(self) -> BatteryStatus:
        raw = self._run("pmset", "-g", "batt")
        percentage_match = re.search(r"(\d+)%", raw)
        percentage = int(percentage_match.group(1)) if percentage_match else None
        charging = None
        if "charging" in raw.lower():
            charging = True
        elif "discharging" in raw.lower():
            charging = False
        power_source = "AC Power" if "AC Power" in raw else ("Battery Power" if "Battery Power" in raw else None)
        return BatteryStatus(percentage=percentage, power_source=power_source, charging=charging, raw=raw)

    def frontmost_application(self) -> AppRecord:
        return self._target_application()

    def frontmost_app(self) -> str:
        return self.frontmost_application().name

    def running_apps(self) -> list[AppRecord]:
        payload = self._run_apps_helper("running")
        if not isinstance(payload, list):
            raise SystemBridgeError("INVALID_APP_RESPONSE", "Could not parse application identity.", "Retry the request.")
        return [self._app_record(item) for item in payload]

    def get_clipboard(self) -> str:
        return self._run("pbpaste")

    def set_clipboard(self, text: str) -> None:
        self._run("pbcopy", input_text=text)

    def show_notification(self, title: str, body: str, subtitle: str | None = None) -> None:
        self._run_osascript(
            [
                "on run argv",
                "set notificationBody to item 1 of argv",
                "set notificationTitle to item 2 of argv",
                "set notificationSubtitle to item 3 of argv",
                'if notificationSubtitle is "" then',
                "display notification notificationBody with title notificationTitle",
                "else",
                "display notification notificationBody with title notificationTitle subtitle notificationSubtitle",
                "end if",
                "end run",
            ],
            args=[body, title, subtitle or ""],
        )

    def open_application(self, application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        # `open -a` also accepts a path, which would launch any bundle on disk, so an
        # application name is resolved to an installed bundle id first and launched by id.
        if bundle_id and bundle_id.strip():
            target_bundle_id = bundle_id.strip()
        elif application and application.strip():
            name = application.strip()
            if "/" in name or name.lower().endswith(".app"):
                raise SystemBridgeError(
                    "INVALID_INPUT",
                    f"Invalid application name '{name}'.",
                    "Use an application name such as Safari or a bundle_id; paths and .app bundles are not accepted.",
                )
            target_bundle_id = self._app_record(self._run_apps_helper("installed-name", name)).bundle_id or ""
        else:
            raise SystemBridgeError("INVALID_INPUT", "application or bundle_id is required.", "Provide an application name or bundle identifier.")
        if not self._BUNDLE_ID_PATTERN.fullmatch(target_bundle_id):
            raise SystemBridgeError("INVALID_INPUT", f"Invalid bundle identifier '{target_bundle_id}'.", "Provide a reverse-DNS bundle identifier such as com.apple.Safari.")
        self._run("open", "-b", target_bundle_id)
        return self._target_application(bundle_id=target_bundle_id)

    def list_settings_domains(self) -> list[dict[str, str]]:
        return [
            {"domain": "NSGlobalDomain", "label": "Global Preferences"},
            {"domain": "com.apple.universalaccess", "label": "Accessibility"},
            {"domain": "com.apple.dock", "label": "Dock"},
            {"domain": "com.apple.finder", "label": "Finder"},
            {"domain": "com.apple.controlcenter", "label": "Control Center"},
            {"domain": "com.apple.screencapture", "label": "Screen Capture"},
        ]

    def appearance_settings(self) -> dict[str, object]:
        global_prefs = self.read_preference_domain("NSGlobalDomain")
        interface_style = str(global_prefs.get("AppleInterfaceStyle", "") or "").strip()
        return {
            "mode": "dark" if interface_style.lower() == "dark" else "light",
            "accent_color": global_prefs.get("AppleAccentColor"),
            "highlight_color": global_prefs.get("AppleHighlightColor"),
            "show_all_extensions": bool(global_prefs.get("AppleShowAllExtensions", False)),
            "icon_theme": global_prefs.get("AppleIconAppearanceTheme"),
        }

    def accessibility_settings(self) -> dict[str, object]:
        prefs = self.read_preference_domain("com.apple.universalaccess")
        return {
            "reduce_motion": bool(prefs.get("reduceMotion", False)),
            "increase_contrast": bool(prefs.get("increaseContrast", False)),
            "reduce_transparency": bool(prefs.get("reduceTransparency", False)),
            "differentiate_without_color": bool(prefs.get("differentiateWithoutColor", False)),
            "hover_text_enabled": bool(prefs.get("hoverTextEnabled", False)),
            "hover_text_color_enabled": bool(prefs.get("hoverColorEnabled", False)),
        }

    def dock_settings(self) -> dict[str, object]:
        prefs = self.read_preference_domain("com.apple.dock")
        return {
            "autohide": bool(prefs.get("autohide", False)),
            "orientation": prefs.get("orientation"),
            "tile_size": prefs.get("tilesize"),
            "magnification_enabled": bool(prefs.get("magnification", False)),
            "large_size": prefs.get("largesize"),
            "minimize_effect": prefs.get("mineffect"),
            "show_recents": bool(prefs.get("show-recents", False)),
        }

    def finder_settings(self) -> dict[str, object]:
        prefs = self.read_preference_domain("com.apple.finder")
        global_prefs = self.read_preference_domain("NSGlobalDomain")
        return {
            "preferred_view_style": prefs.get("FXPreferredViewStyle"),
            "show_path_bar": bool(prefs.get("ShowPathbar", False)),
            "show_status_bar": bool(prefs.get("ShowStatusBar", False)),
            "sort_folders_first": bool(prefs.get("_FXSortFoldersFirst", False)),
            "show_all_extensions": bool(global_prefs.get("AppleShowAllExtensions", False)),
            "show_hidden_files": bool(prefs.get("AppleShowAllFiles", False)),
        }

    def settings_snapshot(self) -> dict[str, dict[str, object]]:
        return {
            "appearance": self.appearance_settings(),
            "accessibility": self.accessibility_settings(),
            "dock": self.dock_settings(),
            "finder": self.finder_settings(),
        }

    def set_appearance_mode(self, mode: str) -> dict[str, object]:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"light", "dark"}:
            raise SystemBridgeError("INVALID_INPUT", "Appearance mode must be 'light' or 'dark'.", "Use light or dark.")
        self._run_osascript(
            [
                "on run argv",
                'tell application "System Events"',
                "tell appearance preferences",
                f"set dark mode to {'true' if normalized_mode == 'dark' else 'false'}",
                "set currentDarkMode to dark mode",
                "end tell",
                "end tell",
                'return (currentDarkMode as string)',
                "end run",
            ]
        )
        observed_mode = self.appearance_settings()["mode"]
        return {"requested_value": normalized_mode, "observed_value": observed_mode, "restarted_processes": []}

    def set_show_all_extensions(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("NSGlobalDomain", "AppleShowAllExtensions", "-bool", "true" if enabled else "false")
        observed_value = self.appearance_settings()["show_all_extensions"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": []}

    def set_show_hidden_files(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.finder", "AppleShowAllFiles", "-bool", "true" if enabled else "false")
        self._restart_process("Finder")
        observed_value = self.finder_settings()["show_hidden_files"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": ["Finder"]}

    def set_finder_path_bar(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.finder", "ShowPathbar", "-bool", "true" if enabled else "false")
        self._restart_process("Finder")
        observed_value = self.finder_settings()["show_path_bar"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": ["Finder"]}

    def set_finder_status_bar(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.finder", "ShowStatusBar", "-bool", "true" if enabled else "false")
        self._restart_process("Finder")
        observed_value = self.finder_settings()["show_status_bar"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": ["Finder"]}

    def set_dock_autohide(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.dock", "autohide", "-bool", "true" if enabled else "false")
        self._restart_process("Dock")
        observed_value = self.dock_settings()["autohide"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": ["Dock"]}

    def set_dock_show_recents(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.dock", "show-recents", "-bool", "true" if enabled else "false")
        self._restart_process("Dock")
        observed_value = self.dock_settings()["show_recents"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": ["Dock"]}

    def set_reduce_motion(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.universalaccess", "reduceMotion", "-bool", "true" if enabled else "false")
        observed_value = self.accessibility_settings()["reduce_motion"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": []}

    def set_increase_contrast(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.universalaccess", "increaseContrast", "-bool", "true" if enabled else "false")
        observed_value = self.accessibility_settings()["increase_contrast"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": []}

    def set_reduce_transparency(self, enabled: bool) -> dict[str, object]:
        self._write_defaults("com.apple.universalaccess", "reduceTransparency", "-bool", "true" if enabled else "false")
        observed_value = self.accessibility_settings()["reduce_transparency"]
        return {"requested_value": enabled, "observed_value": bool(observed_value), "restarted_processes": []}

    def gui_list_menu_bar_items(self, application: str | None = None, bundle_id: str | None = None) -> tuple[AppRecord, list[str]]:
        target_app = self._target_application(application=application, bundle_id=bundle_id)
        raw = self._run_osascript(
            [
                "on run argv",
                "set appName to item 1 of argv",
                'tell application appName to activate',
                "delay 0.1",
                'tell application "System Events"',
                "tell process appName",
                "set itemNames to name of every menu bar item of menu bar 1",
                "end tell",
                "end tell",
                'set AppleScript\'s text item delimiters to linefeed',
                "return itemNames as text",
                "end run",
            ],
            args=[target_app.name],
        )
        return target_app, [item.strip() for item in raw.splitlines() if item.strip()]

    def gui_click_menu_path(self, menu_path: list[str], application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        if len(menu_path) < 2:
            raise SystemBridgeError("INVALID_INPUT", "menu_path must contain at least two items.", "Provide a top-level menu and a target item.")
        target_app = self._gui_input_target(application=application, bundle_id=bundle_id)
        self._run_osascript(
            [
                "on run argv",
                "set targetBundle to last item of argv",
                "set pathCount to (count of argv) - 1",
                *self._FRONTMOST_GUARD,
                'tell application "System Events"',
                "tell (first application process whose bundle identifier is targetBundle)",
                "set currentMenu to menu 1 of menu bar item (item 2 of argv) of menu bar 1",
                "if pathCount is 3 then",
                "click menu item (item 3 of argv) of currentMenu",
                "else",
                "repeat with idx from 3 to (pathCount - 1)",
                "set currentItem to menu item (item idx of argv) of currentMenu",
                "set currentMenu to menu 1 of currentItem",
                "end repeat",
                "click menu item (item pathCount of argv) of currentMenu",
                "end if",
                "end tell",
                "end tell",
                'return "ok"',
                "end run",
            ],
            args=[target_app.name, *menu_path, target_app.bundle_id or ""],
        )
        return target_app

    def gui_press_keys(self, key: str, modifiers: list[str] | None = None, application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        modifier_literals: list[str] = []
        for modifier in modifiers or []:
            normalized = modifier.strip().lower()
            if normalized not in {"command", "control", "option", "shift"}:
                raise SystemBridgeError("INVALID_INPUT", f"Unsupported modifier: {modifier}", "Use command, control, option, or shift.")
            modifier_literals.append(f"{normalized} down")
        modifier_clause = ""
        if modifier_literals:
            modifier_clause = " using {" + ", ".join(modifier_literals) + "}"
        target_app = self._gui_input_target(application=application, bundle_id=bundle_id)
        key_value = key.strip()
        if not key_value:
            raise SystemBridgeError("INVALID_INPUT", "key must not be empty.", "Provide a printable key or supported special key name.")
        normalized_key = key_value.lower()
        key_command = f'keystroke keyText{modifier_clause}'
        if normalized_key in self._SPECIAL_KEY_CODES:
            key_command = f'key code {self._SPECIAL_KEY_CODES[normalized_key]}{modifier_clause}'
        self._run_osascript(
            [
                "on run argv",
                "set keyText to item 2 of argv",
                "set targetBundle to item 3 of argv",
                *self._FRONTMOST_GUARD,
                'tell application "System Events"',
                "tell (first application process whose bundle identifier is targetBundle)",
                key_command,
                "end tell",
                "end tell",
                'return "ok"',
                "end run",
            ],
            args=[target_app.name, key_value, target_app.bundle_id or ""],
        )
        return target_app

    def gui_type_text(self, text: str, application: str | None = None, bundle_id: str | None = None) -> AppRecord:
        target_app = self._gui_input_target(application=application, bundle_id=bundle_id)
        self._run_osascript(
            [
                "on run argv",
                "set typedText to item 2 of argv",
                "set targetBundle to item 3 of argv",
                *self._FRONTMOST_GUARD,
                'tell application "System Events"',
                "tell (first application process whose bundle identifier is targetBundle)",
                "keystroke typedText",
                "end tell",
                "end tell",
                'return "ok"',
                "end run",
            ],
            args=[target_app.name, text, target_app.bundle_id or ""],
        )
        return target_app

    def gui_click_button(
        self,
        label: str | None = None,
        description: str | None = None,
        index: int = 1,
        application: str | None = None,
        bundle_id: str | None = None,
    ) -> AppRecord:
        if not label and not description:
            raise SystemBridgeError("INVALID_INPUT", "label or description is required.", "Provide a button label or accessibility description.")
        if index < 1:
            raise SystemBridgeError("INVALID_INPUT", "index must be at least 1.", "Use a positive button index.")
        target_app = self._gui_input_target(application=application, bundle_id=bundle_id)
        self._run_osascript(
            [
                "on run argv",
                "set buttonName to item 2 of argv",
                "set buttonDescription to item 3 of argv",
                "set buttonIndex to (item 4 of argv) as integer",
                "set targetBundle to item 5 of argv",
                *self._FRONTMOST_GUARD,
                'tell application "System Events"',
                "tell (first application process whose bundle identifier is targetBundle)",
                "tell front window",
                "if buttonName is not \"\" then",
                "set matchingButtons to every button whose name is buttonName",
                "else",
                "set matchingButtons to every button whose description is buttonDescription",
                "end if",
                "if (count of matchingButtons) < buttonIndex then error \"BUTTON_NOT_FOUND\"",
                "click item buttonIndex of matchingButtons",
                "end tell",
                "end tell",
                "end tell",
                'return "ok"',
                "end run",
            ],
            args=[target_app.name, label or "", description or "", str(index), target_app.bundle_id or ""],
        )
        return target_app

    def gui_choose_popup_value(
        self,
        label: str | None,
        value: str,
        description: str | None = None,
        application: str | None = None,
        bundle_id: str | None = None,
    ) -> AppRecord:
        if not label and not description:
            raise SystemBridgeError("INVALID_INPUT", "label or description is required.", "Provide a pop-up label or accessibility description.")
        target_app = self._gui_input_target(application=application, bundle_id=bundle_id)
        self._run_osascript(
            [
                "on run argv",
                "set popupLabel to item 2 of argv",
                "set popupValue to item 3 of argv",
                "set popupDescription to item 4 of argv",
                "set targetBundle to item 5 of argv",
                *self._FRONTMOST_GUARD,
                'tell application "System Events"',
                "tell (first application process whose bundle identifier is targetBundle)",
                "tell front window",
                "if popupLabel is not \"\" then",
                "set targetPopup to first pop up button whose name is popupLabel",
                "else",
                "set targetPopup to first pop up button whose description is popupDescription",
                "end if",
                "click targetPopup",
                "delay 0.1",
                "click menu item popupValue of menu 1 of targetPopup",
                "end tell",
                "end tell",
                "end tell",
                'return "ok"',
                "end run",
            ],
            args=[target_app.name, label or "", value, description or "", target_app.bundle_id or ""],
        )
        return target_app

    def focus_status(self) -> dict[str, object]:
        observed_at = datetime.now().astimezone().isoformat()
        checked_sources: list[str] = []

        for domain in ("com.apple.controlcenter", "com.apple.ncprefs"):
            checked_sources.append(domain)
            try:
                self.read_preference_domain(domain)
            except SystemBridgeError:
                continue

        return {
            "focus_supported": False,
            "focus_active": None,
            "focus_name": None,
            "observed_at": observed_at,
            "source": "unsupported_local_install",
            "confidence": 0.0,
            "notes": [
                "No reliable unsigned local API was found for the active Focus mode on this setup.",
                "Checked preference domains: " + ", ".join(checked_sources) + ".",
                "Notification Center history is also not exposed through this MCP.",
            ],
        }

    def context_snapshot(self) -> dict[str, object]:
        notification_notes = [
            "Notification Center history is not exposed through a reliable unsigned local API on this setup.",
        ]
        battery_payload = None
        frontmost_application = None
        frontmost_app = None
        running_apps_count = None

        try:
            battery_payload = self.battery().model_dump()
        except SystemBridgeError as exc:
            notification_notes.append(f"Battery status unavailable: {exc.error_code}.")

        try:
            frontmost_application = self.frontmost_application()
            frontmost_app = frontmost_application.name
        except SystemBridgeError as exc:
            notification_notes.append(f"Frontmost application unavailable: {exc.error_code}.")

        try:
            running_apps_count = len(self.running_apps())
        except SystemBridgeError as exc:
            notification_notes.append(f"Running application count unavailable: {exc.error_code}.")

        return {
            "observed_at": datetime.now().astimezone().isoformat(),
            "battery": battery_payload,
            "frontmost_app": frontmost_app,
            "frontmost_application": None if frontmost_application is None else frontmost_application.model_dump(),
            "running_apps_count": running_apps_count,
            "focus": self.focus_status(),
            "notification_history_supported": False,
            "notification_history_notes": notification_notes,
        }

    def read_preference_domain(self, domain: str, current_host: bool = False) -> dict[str, object]:
        normalized_domain = domain.strip()
        if not normalized_domain:
            raise SystemBridgeError("INVALID_INPUT", "Preference domain must not be empty.", "Provide a valid macOS defaults domain.")
        if normalized_domain == "-g":
            normalized_domain = "NSGlobalDomain"
        # `defaults export` also accepts a file path or an option, so only plain
        # reverse-DNS domains or single identifiers such as loginwindow (no '/', '~',
        # leading '.' or '-') are passed through.
        if not self._PREFERENCE_DOMAIN_PATTERN.fullmatch(normalized_domain):
            raise SystemBridgeError(
                "INVALID_INPUT",
                f"Invalid preference domain '{normalized_domain}'.",
                "Use NSGlobalDomain, loginwindow or a reverse-DNS domain such as com.apple.dock; file paths are not accepted.",
            )
        command = ["defaults"]
        if current_host:
            command.append("-currentHost")
        command.extend(["export", normalized_domain, "-"])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=False,
                timeout=5,
            )
        except FileNotFoundError as exc:
            raise SystemBridgeError("COMMAND_NOT_FOUND", "Missing defaults command.", "Run this server on macOS.") from exc
        except OSError as exc:
            raise SystemBridgeError("COMMAND_FAILED", f"Could not run defaults: {exc}", "Run this server on macOS.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
            stdout = exc.stdout.decode(errors="replace").strip() if exc.stdout else ""
            raise SystemBridgeError(
                "PREFERENCE_DOMAIN_NOT_FOUND",
                stderr or stdout or f"Could not read preference domain '{normalized_domain}'.",
                "Check the domain name and retry.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemBridgeError("COMMAND_TIMEOUT", f"Timed out while reading '{normalized_domain}'.", "Retry the request.") from exc

        try:
            payload = plistlib.loads(result.stdout)
        except Exception as exc:
            raise SystemBridgeError(
                "INVALID_PREFERENCE_OUTPUT",
                f"Could not decode defaults export for '{normalized_domain}'.",
                "Retry the request or inspect the domain manually.",
            ) from exc
        if not isinstance(payload, dict):
            raise SystemBridgeError(
                "INVALID_PREFERENCE_OUTPUT",
                f"Preference domain '{normalized_domain}' did not export as a dictionary.",
                "Retry the request or inspect the domain manually.",
            )
        return self._json_safe(payload)

    def _json_safe(self, value: object) -> object:
        if isinstance(value, dict):
            return {str(key): self._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, datetime):
            return value.isoformat()
        return value


def build_bridge() -> SystemBridge:
    return SystemBridge()
