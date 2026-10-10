import pytest

from apple_notes_mcp import tools
from apple_notes_mcp.config import load_settings
from apple_notes_mcp.models import AccountInfo, FolderInfo, NoteCapabilities, NoteDetail
from apple_notes_mcp.permissions import SafetyError


class FakeBridge:
    def list_accounts(self):
        return [
            AccountInfo(account_id="acc-1", name="iCloud", upgraded=True, default_folder_id="folder-1"),
        ]

    def list_folders(self, account_name: str | None = None):
        return [
            FolderInfo(
                folder_id="folder-1",
                name="Personal",
                account_id="acc-1",
                account_name=account_name or "iCloud",
                parent_folder_id=None,
                parent_folder_name=None,
                shared=False,
            )
        ]

    def list_notes(self, account_name: str | None = None, folder_id: str | None = None):
        return [
            NoteDetail(
                note_id="note-1",
                title="Dallas trip",
                account_id="acc-1",
                account_name="iCloud",
                folder_id=folder_id or "folder-1",
                folder_name="Personal",
                created_epoch=10,
                modified_epoch=20,
                password_protected=False,
                shared=False,
                tags=["travel"],
                plaintext="Places to visit",
                preview="Places to visit",
                attachment_count=0,
                capabilities=NoteCapabilities(),
                body_html="<div>Places to visit</div>",
                attachments=[],
            )
        ]

    def get_note(self, note_id: str) -> NoteDetail:
        return self.list_notes()[0]

    def create_note(self, *, title: str, folder_id: str, body_html: str | None = None, tags: list[str] | None = None) -> NoteDetail:
        note = self.list_notes(folder_id=folder_id)[0]
        note.title = title
        return note

    def update_note(self, note_id: str, *, title: str | None = None, body_html: str | None = None, folder_id: str | None = None, tags: list[str] | None = None) -> NoteDetail:
        note = self.list_notes(folder_id=folder_id)[0]
        if title is not None:
            note.title = title
        return note

    def delete_note(self, note_id: str) -> bool:
        return True

    def append_to_note(self, note_id: str, body_html: str) -> NoteDetail:
        note = self.get_note(note_id)
        note.body_html = f"{note.body_html}{body_html}"
        note.plaintext = "Places to visitAdd flights"
        return note

    def move_note(self, note_id: str, folder_id: str) -> NoteDetail:
        return self.list_notes(folder_id=folder_id)[0]

    def create_folder(self, *, folder_name: str, account_name: str, parent_folder_id: str | None = None):
        return FolderInfo(
            folder_id="folder-new",
            name=folder_name,
            account_id="acc-1",
            account_name=account_name,
            parent_folder_id=parent_folder_id,
            parent_folder_name=None,
            shared=False,
        )

    def rename_folder(self, folder_id: str, folder_name: str):
        return FolderInfo(
            folder_id=folder_id,
            name=folder_name,
            account_id="acc-1",
            account_name="iCloud",
            parent_folder_id=None,
            parent_folder_name=None,
            shared=False,
        )

    def delete_folder(self, folder_id: str) -> bool:
        return True


def test_notes_health_reports_capabilities(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()

    result = tools.notes_health()

    assert result.ok is True
    assert result.capabilities.supports_attachments is True


def test_create_note_returns_structured_note(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.notes_create_note(title="Dallas trip", folder_id="folder-1", body_html="<div>Places to visit</div>", tags=["travel"])

    assert result.ok is True
    assert result.note.title == "Dallas trip"


def test_search_notes_rejects_invalid_limit(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.notes_search_notes(query="travel", limit=0)

    assert result.ok is False
    assert result.error.error_code == "INVALID_INPUT"


def test_list_folders_accepts_string_limit_and_offset(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.notes_list_folders(limit="1", offset="0")

    assert result.ok is True
    assert result.count == 1


def test_append_to_note_preserves_existing_content(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.notes_append_to_note(note_id="note-1", body_html="<div>Add flights</div>")

    assert result.ok is True
    assert "Places to visit" in result.note.body_html
    assert "Add flights" in result.note.body_html


def test_unscoped_list_notes_honors_folder_allowlist(monkeypatch) -> None:
    # folder_id=None used to skip the folder allowlist and return every note.
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    monkeypatch.setenv("APPLE_NOTES_MCP_ALLOWED_FOLDERS", "Work")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.notes_list_notes()

    assert result.ok is True
    assert result.count == 0


def test_delete_folder_fails_closed(monkeypatch) -> None:
    # Deleting a folder cascades to its notes: blocked outside full_access, and
    # an unresolved folder id must not skip the gate.
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "safe_manage")
    load_settings.cache_clear()
    assert tools.notes_delete_folder(folder_id="folder-1").error.error_code == "WRITE_BLOCKED"

    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    assert tools.notes_delete_folder(folder_id="folder-missing").error.error_code == "FOLDER_NOT_FOUND"
    assert tools.notes_delete_folder(folder_id="folder-1").deleted is True


class NestedFoldersBridge(FakeBridge):
    # "Personal" (folder-1, holds note-1) and "Work" (folder-2) at the top;
    # "Secret" (folder-3) nested inside "Work".
    def list_folders(self, account_name: str | None = None):
        def folder(folder_id: str, name: str, parent: str | None = None) -> FolderInfo:
            return FolderInfo(folder_id=folder_id, name=name, account_id="acc-1", account_name="iCloud", parent_folder_id=parent, parent_folder_name=None, shared=False)

        return [folder("folder-1", "Personal"), folder("folder-2", "Work"), folder("folder-3", "Secret", parent="folder-2")]


def _allow_only_work(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    monkeypatch.setenv("APPLE_NOTES_MCP_ALLOWED_FOLDERS", "Work")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: NestedFoldersBridge())


def test_note_resource_honors_folder_allowlist(monkeypatch) -> None:
    # notes://note/{note_id} returned any note, bypassing the allowlist.
    _allow_only_work(monkeypatch)

    with pytest.raises(SafetyError):
        tools.notes_note_resource("note-1")


def test_move_and_update_check_the_source_folder(monkeypatch) -> None:
    # Only the destination was checked, so a note could be pulled out of a
    # blocked folder into an allowed one.
    _allow_only_work(monkeypatch)

    assert tools.notes_move_note(note_id="note-1", folder_id="folder-2").error.error_code == "FOLDER_BLOCKED"
    assert tools.notes_update_note(note_id="note-1", folder_id="folder-2").error.error_code == "FOLDER_BLOCKED"


def test_delete_folder_checks_nested_subfolders(monkeypatch) -> None:
    # Notes deletes nested subfolders with their parent.
    _allow_only_work(monkeypatch)

    assert tools.notes_delete_folder(folder_id="folder-2").error.error_code == "FOLDER_BLOCKED"


def test_empty_folder_id_means_no_folder(monkeypatch) -> None:
    # Models send "" for unused optional strings; it must not be FOLDER_NOT_FOUND.
    monkeypatch.setenv("APPLE_NOTES_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    assert tools.notes_list_notes(folder_id="").ok is True
    assert tools.notes_update_note(note_id="note-1", title="Trip", folder_id="").ok is True


def test_search_notes_filters_allowlist_before_capping(monkeypatch) -> None:
    # The bridge capped at 100 first, so 100 blocked matches hid allowed ones.
    _allow_only_work(monkeypatch)
    seen: dict[str, object] = {}

    def search_notes(*, query, account_name=None, folder_id=None, limit=25):
        seen["limit"] = limit
        blocked = FakeBridge().list_notes()[0]
        allowed = blocked.model_copy(update={"note_id": "note-work", "folder_id": "folder-2", "folder_name": "Work"})
        return [blocked] * 150 + [allowed]

    monkeypatch.setattr(NestedFoldersBridge, "search_notes", staticmethod(search_notes), raising=False)

    result = tools.notes_search_notes(query="trip")

    assert seen["limit"] is None
    assert [note.note_id for note in result.notes] == ["note-work"]


def teardown_function() -> None:
    load_settings.cache_clear()
