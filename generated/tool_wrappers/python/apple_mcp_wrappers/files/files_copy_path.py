from __future__ import annotations

from typing import Any

from .client import MCPToolCaller, call_tool_json


async def files_copy_path(
    client: MCPToolCaller,
    source: str,
    destination: str
) -> Any:
    """Copy Path

    Copy a file, byte for byte, or a whole folder with everything in it to a new path inside the allowed roots. The destination must not exist; nothing is overwritten.

    Example:
        await files_copy_path(client, source='example_source', destination='example_destination')
    """
    arguments = {
        "source": source,
        "destination": destination,
    }
    payload = {key: value for key, value in arguments.items() if value is not None}
    return await call_tool_json(client, "files_copy_path", payload)
