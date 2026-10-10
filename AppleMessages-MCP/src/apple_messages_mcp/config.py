import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

SafetyMode = Literal["safe_readonly", "safe_manage", "full_access"]
VALID_SAFETY_MODES = frozenset({"safe_readonly", "safe_manage", "full_access"})


@dataclass(frozen=True)
class Settings:
    server_name: str
    version: str
    safety_mode: SafetyMode
    db_path: Path
    log_level: str
    transport: str
    allowed_attachment_roots: tuple[Path, ...]


def _parse_attachment_roots(value: str | None) -> tuple[Path, ...]:
    raw_roots = [raw.strip() for raw in (value or "").split(os.pathsep) if raw.strip()]
    if not raw_roots:
        home = Path.home()
        return (home / "Downloads", home / "Desktop", home / "Documents")
    return tuple(Path(raw).expanduser() for raw in raw_roots)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    raw_safety_mode = os.environ.get("APPLE_MESSAGES_MCP_SAFETY_MODE", "full_access").strip().lower() or "full_access"
    if raw_safety_mode not in VALID_SAFETY_MODES:
        # Fail closed: a mistyped mode must never leave sending enabled.
        raw_safety_mode = "safe_readonly"

    return Settings(
        server_name="Apple Messages MCP",
        version="1.0.5",
        safety_mode=cast(SafetyMode, raw_safety_mode),
        db_path=Path(os.environ.get("APPLE_MESSAGES_MCP_DB_PATH", str(Path.home() / "Library" / "Messages" / "chat.db"))).expanduser(),
        log_level=os.environ.get("APPLE_MESSAGES_MCP_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        transport=os.environ.get("APPLE_MESSAGES_MCP_TRANSPORT", "stdio").strip().lower() or "stdio",
        allowed_attachment_roots=_parse_attachment_roots(os.environ.get("APPLE_MESSAGES_MCP_ALLOWED_ATTACHMENT_ROOTS")),
    )
