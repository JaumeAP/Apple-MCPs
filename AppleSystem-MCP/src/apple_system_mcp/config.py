import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    server_name: str
    version: str
    safety_mode: str
    transport: str
    host: str
    port: int
    log_level: str
    apps_helper_source: Path
    apps_helper_binary: Path
    gui_allowed_apps: frozenset[str]


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    helper_build_dir = Path(
        os.environ.get(
            "APPLE_SYSTEM_MCP_HELPER_BUILD_DIR",
            str(Path.home() / ".apple-mcps" / "build"),
        )
    ).expanduser()
    return Settings(
        server_name="Apple System MCP",
        version="1.0.5",
        safety_mode=os.environ.get("APPLE_SYSTEM_MCP_SAFETY_MODE", "safe_manage").strip().lower() or "safe_manage",
        transport=os.environ.get("APPLE_SYSTEM_MCP_TRANSPORT", "stdio").strip().lower() or "stdio",
        host=os.environ.get("APPLE_SYSTEM_MCP_HOST", "127.0.0.1"),
        port=_parse_int(os.environ.get("APPLE_SYSTEM_MCP_PORT"), 8000),
        log_level=os.environ.get("APPLE_SYSTEM_MCP_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        apps_helper_source=Path(__file__).resolve().parent / "system_apps_bridge.swift",
        apps_helper_binary=helper_build_dir / "apple-system-apps-bridge",
        gui_allowed_apps=frozenset(
            item.strip().lower() for item in os.environ.get("APPLE_SYSTEM_MCP_GUI_ALLOWED_APPS", "").split(",") if item.strip()
        ),
    )
