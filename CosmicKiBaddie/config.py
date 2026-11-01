"""
Cosmic ki baddie — Configuration Module
Server: Side Quest Studio
Supports loading configuration from:
1. Environment variables (`.env` or panel variables)
2. `config.env` (visible file in Bot-Hosting.net / Pterodactyl File Manager)
3. `bot_config.json` (JSON config file in Bot-Hosting.net / Pterodactyl File Manager)
No role IDs, channel IDs, or bot tokens are hardcoded.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

BASE_DIR = Path(__file__).resolve().parent

# 1. Load `.env` and `config.env` (for Bot-Hosting.net web File Manager where dotfiles may be hidden)
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / "config.env")

# 2. Load `bot_config.json` fallback values if not already provided in real environment variables
_JSON_CFG: Dict[str, Any] = {}
_json_path = BASE_DIR / "bot_config.json"
if _json_path.exists():
    try:
        _JSON_CFG = json.loads(_json_path.read_text(encoding="utf-8"))
    except Exception:
        _JSON_CFG = {}


def _get_cfg(key: str, default: str = "") -> str:
    """Resolve config value from OS env -> config.env -> bot_config.json -> default."""
    env_val = os.getenv(key)
    Placeholder_tokens = {
        "",
        "your_discord_bot_token_here",
        "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE",
    }
    if env_val is not None and env_val.strip() not in Placeholder_tokens:
        return env_val.strip()
    json_val = _JSON_CFG.get(key)
    if json_val is not None and str(json_val).strip() not in Placeholder_tokens:
        return str(json_val).strip()
    return env_val.strip() if env_val is not None else default


@dataclass(frozen=True)
class ThemePalette:
    """Brand color palette for Cosmic ki baddie (Side Quest Studio)."""

    PRIMARY: int = 0x8B5CF6      # Cosmic Violet
    SECONDARY: int = 0xEC4899    # Nebula Pink
    ACCENT: int = 0x06B6D4       # Starlight Cyan
    SUCCESS: int = 0x10B981      # Emerald Green
    WARNING: int = 0xF59E0B      # Solar Amber
    DANGER: int = 0xEF4444       # Supernova Red
    INFO: int = 0x3B82F6         # Astral Blue
    NEUTRAL: int = 0x1E1B4B      # Deep Space Indigo


@dataclass
class BotConfig:
    """Central runtime configuration loaded from environment or Bot-Hosting panel files."""

    bot_name: str = field(default_factory=lambda: _get_cfg("BOT_NAME", "Cosmic ki baddie"))
    community_name: str = field(default_factory=lambda: _get_cfg("COMMUNITY_NAME", "Side Quest Studio"))
    token: str = field(default_factory=lambda: _get_cfg("DISCORD_TOKEN", ""))
    default_prefix: str = field(default_factory=lambda: _get_cfg("DEFAULT_PREFIX", "!"))
    owner_ids: List[int] = field(
        default_factory=lambda: [
            int(x.strip())
            for x in _get_cfg("OWNER_IDS", "").split(",")
            if x.strip().isdigit()
        ]
    )
    database_url: str = field(
        default_factory=lambda: _get_cfg(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / 'data' / 'cosmic_ki_baddie.db'}",
        )
    )
    backup_dir: Path = field(
        default_factory=lambda: Path(
            _get_cfg("BACKUP_DIR", str(BASE_DIR / "data" / "backups"))
        )
    )
    transcript_dir: Path = field(
        default_factory=lambda: Path(
            _get_cfg("TRANSCRIPT_DIR", str(BASE_DIR / "data" / "transcripts"))
        )
    )
    log_level: str = field(default_factory=lambda: _get_cfg("LOG_LEVEL", "INFO").upper())
    sync_commands_on_startup: bool = field(
        default_factory=lambda: _get_cfg("SYNC_COMMANDS_ON_STARTUP", "true").lower() in ("1", "true", "yes")
    )
    dev_guild_id: Optional[int] = field(
        default_factory=lambda: int(_get_cfg("DEV_GUILD_ID"))
        if _get_cfg("DEV_GUILD_ID", "").isdigit()
        else None
    )
    status_text: str = field(
        default_factory=lambda: _get_cfg("BOT_STATUS", "Side Quest Studio | /help")
    )
    max_Backup_retention: int = field(
        default_factory=lambda: int(_get_cfg("MAX_BACKUP_RETENTION", "15"))
    )

    # Protected commands that can NEVER be disabled via /command disable to prevent server bricking
    protected_commands: tuple[str, ...] = (
        "command",
        "command enable",
        "command disable",
        "command list",
        "command permissions",
        "command cooldown",
        "command settings",
        "permissions",
        "permissions panel",
        "permissions set",
        "permissions remove",
        "permissions view",
        "permissions reset",
        "safemode",
        "protection",
        "setup",
        "help",
        "prefix",
    )

    # All modular extensions loaded by CosmicBot
    initial_extensions: tuple[str, ...] = (
        "cogs.permissions",
        "cogs.command_manager",
        "cogs.moderation",
        "cogs.logging",
        "cogs.roles",
        "cogs.automation",
        "cogs.autoresponder",
        "cogs.welcome",
        "cogs.tickets",
        "cogs.embeds",
        "cogs.antinuke",
        "cogs.protection",
        "cogs.giveaways",
        "cogs.events",
        "cogs.polls",
        "cogs.setup_wizard",
        "cogs.help",
    )


THEME = ThemePalette()
CONFIG = BotConfig()
