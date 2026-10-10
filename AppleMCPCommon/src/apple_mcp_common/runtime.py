from __future__ import annotations

from typing import Any

from mcp_types.version import MODERN_PROTOCOL_VERSIONS

CANONICAL_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# Exactly Foundation's CharacterSet.whitespacesAndNewlines (enumerated with
# swiftc on macOS 26). It differs from str.strip(): it trims U+200B and keeps
# U+001C-U+001F.
SWIFT_WHITESPACE = "".join(
    map(chr, (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200C), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000))
)


def swift_trim(value: str) -> str:
    """Trim the way the Swift helpers trim ids and strings.

    Python must see the same id the helper will act on: an allowlist lookup on
    an id the helper later trims differently is an allowlist bypass.
    """
    return value.strip(SWIFT_WHITESPACE)


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
