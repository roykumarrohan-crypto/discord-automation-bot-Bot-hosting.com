"""
Cosmic ki baddie — Hybrid Command & App Command Check Decorators
Integrates dynamic cooldowns, channel restrictions, and role permissions across both
prefix commands (`!cmd`) and slash commands (`/cmd`).
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Tuple, TypeVar

import discord
from discord.ext import commands

T = TypeVar("T")


class DynamicCommandCooldownManager:
    """Tracks per-guild, per-command, per-user cooldowns configured dynamically in Discord."""

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[int, str, int], float] = {}

    def check_and_trigger(
        self, guild_id: int, command_name: str, user_id: int, cooldown_seconds: int
    ) -> float:
        """
        Return remaining cooldown seconds if user is on cooldown, or 0.0 and record invocation.
        """
        if cooldown_seconds <= 0:
            return 0.0
        now = time.monotonic()
        key = (guild_id, command_name.lower().strip(), user_id)
        expires_at = self._buckets.get(key, 0.0)
        if now < expires_at:
            return round(expires_at - now, 1)
        self._buckets[key] = now + cooldown_seconds
        return 0.0


COOLDOWN_MANAGER = DynamicCommandCooldownManager()


def require_Write_or_tier(default_tier: str = "member") -> Callable[[T], T]:
    """
    Decorator for hybrid/prefix/slash commands that marks the default tier metadata
    (actual enforcement happens globally in `CosmicBot.global_command_check` so every
    command is automatically protected).
    """

    def decorator(func: T) -> T:
        setattr(func, "__cosmic_tier__", default_tier)
        return func

    return decorator
