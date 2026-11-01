"""
Cosmic ki baddie — Standardized Embed Factory & JSON Serializer
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from config import CONFIG, THEME
from utils.helpers import parse_hex_color, render_template


def make_embed(
    title: Optional[str] = None,
    description: Optional[str] = None,
    color: int = THEME.PRIMARY,
    footer: Optional[str] = None,
    timestamp: bool = True,
    author_name: Optional[str] = None,
    author_icon: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    image_url: Optional[str] = None,
) -> discord.Embed:
    """Create a branded Cosmic ki baddie Discord Embed."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc) if timestamp else None,
    )
    embed.set_footer(text=footer or f"{CONFIG.bot_name} • {CONFIG.community_name}")
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    if image_url:
        embed.set_image(url=image_url)
    return embed


def success_embed(title: str, description: str) -> discord.Embed:
    """Create a branded emerald success embed."""
    return make_embed(title=f"✅ {title}", description=description, color=THEME.SUCCESS)


def error_embed(title: str, description: str) -> discord.Embed:
    """Create a branded supernova red error embed."""
    return make_embed(title=f"⚠️ {title}", description=description, color=THEME.DANGER)


def warning_embed(title: str, description: str) -> discord.Embed:
    """Create a branded amber warning embed."""
    return make_embed(title=f"⚡ {title}", description=description, color=THEME.WARNING)


def info_embed(title: str, description: str) -> discord.Embed:
    """Create a branded cosmic info embed."""
    return make_embed(title=f"✨ {title}", description=description, color=THEME.PRIMARY)


def embed_from_dict(
    data: Dict[str, Any],
    member: Optional[discord.abc.User] = None,
    guild: Optional[discord.Guild] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> discord.Embed:
    """
    Construct a `discord.Embed` from a stored JSON dictionary, interpolating dynamic variables.
    """
    title = render_template(str(data.get("title") or ""), member, guild, extra) or None
    description = render_template(str(data.get("description") or ""), member, guild, extra) or None
    raw_color = data.get("color", THEME.PRIMARY)
    color = raw_color if isinstance(raw_color, int) else parse_hex_color(str(raw_color), THEME.PRIMARY)

    use_ts = bool(data.get("timestamp", True))
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc) if use_ts else None,
    )

    author_name = render_template(str(data.get("author_name") or ""), member, guild, extra)
    author_icon = render_template(str(data.get("author_icon") or ""), member, guild, extra) or None
    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon)

    footer_text = render_template(
        str(data.get("footer_text") or f"{CONFIG.bot_name} • {CONFIG.community_name}"),
        member,
        guild,
        extra,
    )
    footer_icon = render_template(str(data.get("footer_icon") or ""), member, guild, extra) or None
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_icon)

    thumb = render_template(str(data.get("thumbnail_url") or ""), member, guild, extra)
    if thumb and thumb.startswith(("http://", "https://")):
        embed.set_thumbnail(url=thumb)

    image = render_template(str(data.get("image_url") or ""), member, guild, extra)
    if image and image.startswith(("http://", "https://")):
        embed.set_image(url=image)

    fields: List[Dict[str, Any]] = data.get("fields") or []
    for fld in fields[:25]:
        fname = render_template(str(fld.get("name") or "\u200b"), member, guild, extra)
        fval = render_template(str(fld.get("value") or "\u200b"), member, guild, extra)
        finline = bool(fld.get("inline", False))
        embed.add_field(name=fname[:256], value=fval[:1024], inline=finline)

    return embed


def embed_to_dict(embed: discord.Embed) -> Dict[str, Any]:
    """Serialize a `discord.Embed` into a portable JSON-compatible dictionary."""
    return {
        "title": embed.title or "",
        "description": embed.description or "",
        "color": embed.color.value if embed.color else THEME.PRIMARY,
        "timestamp": embed.timestamp is not None,
        "author_name": embed.author.name if embed.author else "",
        "author_icon": embed.author.icon_url if embed.author else "",
        "footer_text": embed.footer.text if embed.footer else "",
        "footer_icon": embed.footer.icon_url if embed.footer else "",
        "thumbnail_url": embed.thumbnail.url if embed.thumbnail else "",
        "image_url": embed.image.url if embed.image else "",
        "fields": [
            {"name": f.name, "value": f.value, "inline": f.inline}
            for f in embed.fields
        ],
    }
