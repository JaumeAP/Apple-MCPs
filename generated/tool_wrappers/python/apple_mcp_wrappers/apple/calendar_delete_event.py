from __future__ import annotations

from typing import Any

from .client import MCPToolCaller, call_tool_json


async def calendar_delete_event(
    client: MCPToolCaller,
    event_id: str
) -> Any:
    """Calendar Delete Event

    Delete a calendar event by event_id. With native Calendar access, a recurring event is deleted for one occurrence only, not the rest of the series: the occurrence named by an event_id from list or get ("<id>@<original start>"), or the first occurrence for a bare id. The automation fallback may delete the whole series.

    Example:
        await calendar_delete_event(client, event_id='example_event_id')
    """
    arguments = {
        "event_id": event_id,
    }
    payload = {key: value for key, value in arguments.items() if value is not None}
    return await call_tool_json(client, "calendar_delete_event", payload)
