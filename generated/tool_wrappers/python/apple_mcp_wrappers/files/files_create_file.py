from __future__ import annotations

from typing import Any

from .client import MCPToolCaller, call_tool_json


async def files_create_file(
    client: MCPToolCaller,
    path: str,
    text: str | None = None,
    content_base64: str | None = None
) -> Any:
    """Create File

    Create a new file inside the allowed roots. Pass text for UTF-8 content or content_base64 for binary content, exactly one of them. The parent folder must exist and the path must not. Never overwrites.

    Example:
        await files_create_file(client, path='/path/to/item', text='example_text')
    """
    arguments = {
        "path": path,
        "text": text,
        "content_base64": content_base64,
    }
    payload = {key: value for key, value in arguments.items() if value is not None}
    return await call_tool_json(client, "files_create_file", payload)
