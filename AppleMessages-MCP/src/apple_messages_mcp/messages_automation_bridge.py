from __future__ import annotations

import subprocess

_OSASCRIPT_TIMEOUT_SECONDS = 30


class MessagesAutomationBridgeError(Exception):
    def __init__(self, error_code: str, message: str, suggestion: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.suggestion = suggestion


class MessagesAutomationBridge:
    def automation_accessible(self) -> bool:
        accessible, _ = self.automation_access_diagnostic()
        return accessible

    def automation_access_diagnostic(self) -> tuple[bool, MessagesAutomationBridgeError | None]:
        try:
            self._run_script('tell application "Messages" to count of every service')
            return True, None
        except MessagesAutomationBridgeError as exc:
            return False, exc

    def send_message(self, recipient: str, text: str) -> dict[str, str | bool | None]:
        if not recipient.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "recipient must not be empty", "Provide an explicit iMessage address or phone number.")
        if not text.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "text must not be empty", "Provide a non-empty message body.")
        script = '''
        on run argv
            tell application "Messages"
                set targetService to first service whose service type = iMessage
                set targetBuddy to buddy (item 1 of argv) of targetService
                send (item 2 of argv) to targetBuddy
            end tell
        end run
        '''
        self._run_script(script, recipient, text)
        return {
            "sent": True,
            "recipient": recipient,
            "text": text,
            "service_name": "iMessage",
        }

    def send_to_group(self, chat_id: str, text: str) -> dict[str, str | bool | None]:
        if not chat_id.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "chat_id must not be empty", "Provide a valid chat_id from conversation history.")
        if not text.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "text must not be empty", "Provide a non-empty message body.")

        script = '''
        on run argv
            tell application "Messages"
                set targetChat to chat id (item 1 of argv)
                send (item 2 of argv) to targetChat
            end tell
        end run
        '''
        self._run_script(script, chat_id, text)
        return {
            "sent": True,
            "chat_id": chat_id,
            "text": text,
        }

    def send_attachment(self, recipient: str, file_path: str, text: str | None = None) -> dict[str, str | bool | None]:
        if not recipient.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "recipient must not be empty", "Provide an explicit iMessage address or phone number.")
        if not file_path.strip():
            raise MessagesAutomationBridgeError("INVALID_INPUT", "file_path must not be empty", "Provide a valid file path.")

        script = '''
        on run argv
            tell application "Messages"
                set targetService to first service whose service type = iMessage
                set targetBuddy to buddy (item 1 of argv) of targetService
                send (POSIX file (item 2 of argv)) to targetBuddy
                if (item 3 of argv) is not "" then
                    send (item 3 of argv) to targetBuddy
                end if
            end tell
        end run
        '''
        self._run_script(script, recipient, file_path, text if text and text.strip() else "")
        return {
            "sent": True,
            "recipient": recipient,
            "file_path": file_path,
            "text": text,
        }

    def _run_script(self, script: str, *args: str) -> str:
        # Untrusted values travel as `on run argv` arguments, never inside the script source.
        command = ["osascript", "-e", script]
        if args:
            command.append("--")
            command.extend(args)
        try:
            completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=_OSASCRIPT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise MessagesAutomationBridgeError(
                "AUTOMATION_TIMEOUT",
                f"Messages automation did not finish within {_OSASCRIPT_TIMEOUT_SECONDS} seconds. The message may or may not have been sent.",
                "Verify in Messages.app whether the message went out before retrying, to avoid sending a duplicate.",
            ) from exc
        except OSError as exc:
            raise MessagesAutomationBridgeError(
                "OSASCRIPT_UNAVAILABLE",
                f"Could not run 'osascript': {exc}.",
                "This server requires macOS with osascript available.",
            ) from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "Messages automation failed."
            lowered = message.lower()
            if "not authorized" in lowered or "automation" in lowered:
                raise MessagesAutomationBridgeError(
                    "PERMISSION_DENIED",
                    "macOS denied automation access to Messages.",
                    "Allow automation access for the host app or terminal and retry.",
                )
            raise MessagesAutomationBridgeError("AUTOMATION_FAILED", message, "Inspect Messages.app state and retry.")
        return completed.stdout.strip()
