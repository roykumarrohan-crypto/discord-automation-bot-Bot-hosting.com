"""
Cosmic ki baddie — General Helper Utilities
Duration parsing, timestamp formatting, template variable interpolation, and level XP math.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import discord

DURATION_REGEX = re.compile(
    r"^\s*(?:(\d+)\s*w(?:eeks?)?)?\s*"
    r"(?:(\d+)\s*d(?:ays?)?)?\s*"
    r"(?:(\d+)\s*h(?:ours?|r|rs)?)?\s*"
    r"(?:(\d+)\s*m(?:inutes?|in|ins)?)?\s*"
    r"(?:(\d+)\s*s(?:econds?|ec|ecs)?)?\s*$",
    re.IGNORECASE,
)


def parse_duration(raw: Optional[str]) -> Optional[int]:
    """
    Parse a human-friendly duration string (e.g. `10m`, `2h30m`, `7d`, `1w`, `3600`)
    into total seconds. Returns None if invalid or empty.
    """
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.isdigit():
        return int(cleaned)
    match = DURATION_REGEX.match(cleaned)
    if not match or not any(match.groups()):
        return None
    weeks, days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    total = (
        weeks * 604800
        + days * 86400
        + hours * 3600
        + minutes * 60
        + seconds
    )
    return total if total > 0 else None


def format_duration(seconds: Optional[int]) -> str:
    """Format a duration in seconds into a readable string like `2d 4h 15m`."""
    if seconds is None or seconds <= 0:
        return "Permanent"
    rem = int(seconds)
    days, rem = divmod(rem, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs and not days:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


def parse_hex_color(raw: str, default: int = 0x8B5CF6) -> int:
    """Parse a hex color string (`#8B5CF6`, `0x8B5CF6`, `8B5CF6`) into an integer."""
    if not raw:
        return default
    cleaned = raw.strip().lstrip("#").lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    try:
        val = int(cleaned, 16)
        return max(0x000000, min(0xFFFFFF, val))
    except ValueError:
        return default


def xp_for_level(level: int) -> int:
    """Calculate total cumulative XP required to reach `level`."""
    if level <= 0:
        return 0
    return int(50 * (level ** 2) + 100 * level)


def level_from_xp(xp: int) -> int:
    """Calculate a member's level from their total cumulative XP."""
    if xp <= 0:
        return 0
    level = int((-100 + math.sqrt(10000 + 200 * xp)) // 100)
    return max(0, level)


def render_template(
    template: str,
    member: Optional[discord.abc.User] = None,
    guild: Optional[discord.Guild] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Safely interpolate placeholders such as `{user}`, `{user.mention}`, `{user.name}`,
    `{user.id}`, `{server}`, `{member_count}`, `{level}`, etc.
    """
    if not template:
        return ""
    replacements: Dict[str, str] = {}
    if member is not None:
        created_at = getattr(member, "created_at", None)
        replacements.update(
            {
                "{user}": str(member),
                "{user.mention}": getattr(member, "mention", f"<@{member.id}>"),
                "{user.name}": getattr(member, "name", str(member)),
                "{user.display_name}": getattr(member, "display_name", getattr(member, "name", str(member))),
                "{user.id}": str(member.id),
                "{user.avatar}": str(member.display_avatar.url) if getattr(member, "display_avatar", None) else "",
                "{user.created_at}": discord.utils.format_dt(created_at, "R") if created_at else "Unknown",
            }
        )
    if guild is not None:
        replacements.update(
            {
                "{server}": guild.name,
                "{server.name}": guild.name,
                "{server.id}": str(guild.id),
                "{member_count}": str(guild.member_count or len(guild.members)),
            }
        )
    if extra:
        for k, v in extra.items():
            key = k if k.startswith("{") else f"{{{k}}}"
            replacements[key] = str(v)

    result = template
    for placeholder, val in replacements.items():
        result = result.replace(placeholder, val)
    return result


def build_progress_bar(current: int, total: int, length: int = 12) -> str:
    """Build a visual Unicode progress bar."""
    if total <= 0:
        return "▱" * length + " 0%"
    ratio = max(0.0, min(1.0, current / total))
    filled = int(round(ratio * length))
    bar = "▰" * filled + "▱" * (length - filled)
    return f"{bar} {int(ratio * 100)}%"
