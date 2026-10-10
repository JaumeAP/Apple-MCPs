import json

from apple_reminders_mcp import tools
from apple_reminders_mcp.config import load_settings
from apple_reminders_mcp.models import ReminderDetail, ReminderListInfo


class FakeBridge:
    def create_list(self, title: str):
        return tools.ReminderListMutationResponse(list_id="list-new", title=title, created=True)

    def list_lists(self):
        return [
            ReminderListInfo(
                list_id="list-1",
                title="Chores",
                source_title="iCloud",
                allows_content_modifications=True,
                color_hex="#D9A69F",
            )
        ]

    def list_reminders(self, **kwargs):
        return []

    def get_reminder(self, reminder_id: str) -> ReminderDetail:
        return ReminderDetail(
            reminder_id=reminder_id,
            title="Trash day",
            list_id="list-1",
            list_name="Chores",
            due_date="2026-03-28T22:30:00Z",
            due_all_day=False,
            remind_at="2026-03-28T22:30:00Z",
            priority=1,
            completed=False,
            completion_date=None,
            notes="Bring bins in",
            creation_date="2026-03-20T10:00:00Z",
            modification_date="2026-03-27T10:00:00Z",
        )

    def create_reminder(self, **kwargs) -> ReminderDetail:
        return self.get_reminder("x-apple-reminder://new")

    def update_reminder(self, reminder_id: str, **kwargs) -> ReminderDetail:
        return self.get_reminder(reminder_id)

    def set_completed(self, reminder_id: str, completed: bool) -> ReminderDetail:
        detail = self.get_reminder(reminder_id)
        detail.completed = completed
        return detail

    def delete_reminder(self, reminder_id: str) -> bool:
        return True

    def delete_list(self, list_id: str):
        return tools.DeleteReminderListResponse(list_id=list_id, deleted=True)


def test_create_reminder_returns_structured_payload(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "safe_manage")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.reminders_create_reminder(title="Trash day", list_id="list-1")

    assert result.ok is True
    assert result.reminder.list_name == "Chores"


def test_list_reminders_rejects_bad_limit(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "safe_manage")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.reminders_list_reminders(limit=0)

    assert result.ok is False
    assert result.error.error_code == "INVALID_INPUT"


def test_reminders_main_exists() -> None:
    assert callable(tools.main)


def test_list_reminders_accepts_string_limit(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "safe_manage")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.reminders_list_reminders(limit="5")

    assert result.ok is True


def test_create_reminder_accepts_string_priority(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "safe_manage")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.reminders_create_reminder(title="Trash day", list_id="list-1", priority="2")

    assert result.ok is True


def test_create_and_delete_list_return_structured_payload(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    created = tools.reminders_create_list("General")
    deleted = tools.reminders_delete_list("list-1")

    assert created.ok is True
    assert created.title == "General"
    assert deleted.ok is True
    assert deleted.deleted is True


def test_create_reminder_rejects_subtasks_until_supported(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "full_access")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: FakeBridge())

    result = tools.reminders_create_reminder(
        title="Child",
        list_id="list-1",
        parent_reminder_id="x-apple-reminder://parent",
    )

    assert result.ok is False
    assert result.error.error_code == "SUBTASKS_UNSUPPORTED"


class TwoListBridge(FakeBridge):
    def __init__(self) -> None:
        self.deleted_list_ids: list[str] = []
        self.updated: list[dict] = []

    def list_lists(self):
        return [
            *super().list_lists(),
            ReminderListInfo(list_id="list-2", title="Private", source_title="iCloud", allows_content_modifications=True),
        ]

    def list_reminders(self, **kwargs):
        chores = self.get_reminder("x-apple-reminder://chores")
        private = chores.model_copy(update={"reminder_id": "x-apple-reminder://private", "list_id": "list-2", "list_name": "Private"})
        return [chores, private]

    def delete_list(self, list_id: str):
        self.deleted_list_ids.append(list_id)
        return super().delete_list(list_id)

    def update_reminder(self, reminder_id: str, **kwargs) -> ReminderDetail:
        self.updated.append(kwargs)
        return super().update_reminder(reminder_id, **kwargs)


def test_delete_list_applies_allowlist_and_skips_unknown_ids(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "full_access")
    monkeypatch.setenv("APPLE_REMINDERS_MCP_ALLOWED_LISTS", "Chores")
    load_settings.cache_clear()
    bridge = TwoListBridge()
    monkeypatch.setattr(tools, "_bridge", lambda: bridge)

    blocked = tools.reminders_delete_list("list-2")
    calendar_id = tools.reminders_delete_list("event-calendar-id")

    assert blocked.ok is False
    assert blocked.error.error_code == "LIST_BLOCKED"
    assert calendar_id.ok is True
    assert calendar_id.deleted is False
    assert bridge.deleted_list_ids == []


def test_update_reminder_checks_destination_list_against_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_SAFETY_MODE", "safe_manage")
    monkeypatch.setenv("APPLE_REMINDERS_MCP_ALLOWED_LISTS", "Chores")
    load_settings.cache_clear()
    bridge = TwoListBridge()
    monkeypatch.setattr(tools, "_bridge", lambda: bridge)

    result = tools.reminders_update_reminder("x-apple-reminder://chores", list_id="list-2")

    assert result.ok is False
    assert result.error.error_code == "LIST_BLOCKED"
    assert bridge.updated == []


def test_resources_hide_lists_outside_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("APPLE_REMINDERS_MCP_ALLOWED_LISTS", "Chores")
    load_settings.cache_clear()
    monkeypatch.setattr(tools, "_bridge", lambda: TwoListBridge())

    lists = json.loads(tools.reminders_lists_resource())
    today = json.loads(tools.reminders_today_resource())

    assert [item["title"] for item in lists["lists"]] == ["Chores"]
    assert [item["list_name"] for item in today["reminders"]] == ["Chores"]


def teardown_function() -> None:
    load_settings.cache_clear()
