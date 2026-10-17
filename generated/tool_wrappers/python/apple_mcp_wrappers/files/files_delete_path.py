from __future__ import annotations

from typing import Any

from .client import MCPToolCaller, call_tool_json


async def files_delete_path(
    client: MCPToolCaller,
    path: str,
    recursive: bool | None = None
) -> Any:
    """Delete Path

    Delete a file or a folder inside the allowed roots. A folder with content needs recursive=true, which deletes it and everything in it. An allowed root is never deleted. Requires full_access safety mode.

    Example:
        await files_delete_path(client, path='/path/to/item', recursive=False)
    """
    arguments = {
        "path": path,
        "recursive": recursive,
    }
    payload = {key: value for key, value in arguments.items() if value is not None}
    return await call_tool_json(client, "files_delete_path", payload)
