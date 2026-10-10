from __future__ import annotations

from typing import Any

from mcp_types.version import MODERN_PROTOCOL_VERSIONS

CANONICAL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def require_loopback_host(host: str) -> str:
    """Return a canonical loopback HTTP host or reject it.

    Only the spellings the MCP SDK recognises for its DNS-rebinding guard are
    accepted; other loopback forms (127.1, ::ffff:127.0.0.1, ...) would bind
    locally but silently disable that guard.
    """
    normalized = host.strip().lower()
    if normalized not in CANONICAL_LOOPBACK_HOSTS:
        raise ValueError(f"Streamable HTTP host must be a loopback address (127.0.0.1, ::1 or localhost), got {host!r}")
    return normalized


def _uses_modern_subscriptions(ctx: Any) -> bool:
    return ctx.protocol_version in MODERN_PROTOCOL_VERSIONS


async def notify_resource_updated(ctx: Any, uri: str) -> None:
    """Notify the resource update over the negotiated protocol's API."""
    if _uses_modern_subscriptions(ctx):
        await ctx.notify_resource_updated(uri)
    else:
        await ctx.request_context.session.send_resource_updated(uri)


async def notify_resources_changed(ctx: Any) -> None:
    """Notify the resource-list change over the negotiated protocol's API."""
    if _uses_modern_subscriptions(ctx):
        await ctx.notify_resources_changed()
    else:
        await ctx.request_context.session.send_resource_list_changed()
