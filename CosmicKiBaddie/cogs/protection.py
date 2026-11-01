"""
Cosmic ki baddie — Safe Mode & Server Protection Posture (`cogs/protection.py`)
Implements:
- SAFE MODE (`/safemode enable`, `/safemode disable`, `/safemode status`):
  Temporary security lockdown that disables destructive bot commands, restricts
  sensitive management actions, notifies security staff, preserves normal member
  communication, and requires authorized staff to deactivate.
- SERVER PROTECTION MODE (`/protection mode <normal|enhanced|emergency>`,
  `/protection lockdown`, `/protection unlockdown`, `/protection alert_channel`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed

if TYPE_CHECKING:
    from main import CosmicBot


class ProtectionCog(commands.Cog, name="Protection"):
    """Safe Mode management, 3-tier Server Protection posture, and emergency lockdowns."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def _notify_security_channel(
        self, guild: discord.Guild, title: str, description: str, color: int = THEME.WARNING
    ) -> None:
        cfg = await self.bot.db.ensure_guild(guild.id)
        ch_id = cfg.get("security_alert_channel_id") or cfg.get("modlog_channel_id")
        if not ch_id:
            return
        ch = guild.get_channel(int(ch_id))
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=make_embed(title=title, description=description, color=color))
            except Exception:
                pass

    # =========================================================================
    # /safemode Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="safemode",
        description="Manage Safe Mode temporary security lockdown for Side Quest Studio.",
        invoke_without_command=True,
    )
    async def safemode_group(self, ctx: commands.Context[Any]) -> None:
        await self.safemode_status(ctx)

    @safemode_group.command(
        name="status", description="Check whether Safe Mode is currently active."
    )
    async def safemode_status(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_guild(ctx.guild.id)
        active = bool(cfg.get("safe_mode"))
        if active:
            await ctx.reply(
                embed=warning_embed(
                    "🛡️ Safe Mode is ACTIVE",
                    f"Activated by <@{cfg.get('safe_mode_activated_by')}> at `{cfg.get('safe_mode_activated_at', 'Unknown')[:19]}`.\n"
                    f"Destructive commands (`ban`, `kick`, `purge`, `role delete`, etc.) are restricted to Server Administrators while normal member chat remains open.",
                )
            )
        else:
            await ctx.reply(
                embed=info_embed(
                    "Safe Mode is Inactive",
                    "Server operations are running normally. Use `/safemode enable` to engage a temporary security lockdown.",
                )
            )

    @safemode_group.command(
        name="enable",
        description="Activate Safe Mode to restrict destructive commands and notify security staff.",
    )
    @app_commands.describe(reason="Reason for activating Safe Mode")
    async def safemode_enable(
        self,
        ctx: commands.Context[Any],
        *,
        reason: str = "Manual security lockdown initiated by staff",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        await self.bot.db.update_guild_config(
            ctx.guild.id,
            safe_mode=1,
            safe_mode_activated_by=ctx.author.id,
            safe_mode_activated_at=now_iso,
        )
        await self._notify_security_channel(
            ctx.guild,
            "🛡️ SAFE MODE ENGAGED",
            f"**Activated By:** {ctx.author.mention}\n"
            f"**Reason:** {reason}\n"
            f"Sensitive management and destructive bot commands are now restricted to Server Administrators.",
            color=THEME.DANGER,
        )
        await ctx.reply(
            embed=warning_embed(
                "🛡️ Safe Mode Activated",
                f"Dangerous bot commands and sensitive management actions are now restricted.\n"
                f"Normal member communication is preserved.\n"
                f"**Reason:** {reason}",
            )
        )

    @safemode_group.command(
        name="disable",
        description="Deactivate Safe Mode and restore standard command permissions (Authorized Admin only).",
    )
    async def safemode_disable(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        cfg = await self.bot.db.ensure_guild(ctx.guild.id)
        admin_role_id = cfg.get("admin_role_id")
        is_authorized = (
            self.bot.permissions_engine.is_emergency_admin(ctx.author)
            or (admin_role_id and int(admin_role_id) in {r.id for r in ctx.author.roles})
        )
        if not is_authorized:
            await ctx.reply(
                embed=error_embed(
                    "Authorization Required",
                    "Only Server Administrators or the configured Admin Role can deactivate Safe Mode.",
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.update_guild_config(
            ctx.guild.id,
            safe_mode=0,
            safe_mode_activated_by=None,
            safe_mode_activated_at=None,
        )
        await self._notify_security_channel(
            ctx.guild,
            "✅ SAFE MODE DEACTIVATED",
            f"Safe Mode was lifted by {ctx.author.mention}. Normal staff operations resumed.",
            color=THEME.SUCCESS,
        )
        await ctx.reply(
            embed=success_embed(
                "Safe Mode Deactivated",
                f"Safe Mode has been lifted by {ctx.author.mention}. All normal staff permissions are restored.",
            )
        )

    # =========================================================================
    # /protection Command Group (Normal / Enhanced / Emergency Modes & Lockdown)
    # =========================================================================
    @commands.hybrid_group(
        name="protection",
        description="Manage Server Protection Mode (Normal, Enhanced, Emergency) and emergency lockdowns.",
        invoke_without_command=True,
    )
    async def protection_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_guild(ctx.guild.id)
        mode = str(cfg.get("protection_mode") or "normal").upper()
        safe = "ACTIVE 🛡️" if cfg.get("safe_mode") else "Inactive"
        alert_ch = (
            f"<#{cfg['security_alert_channel_id']}>"
            if cfg.get("security_alert_channel_id")
            else "*Uses Moderation Log*"
        )
        embed = make_embed(
            title=f"🛡️ Server Protection Posture — {ctx.guild.name}",
            description=(
                f"• **Protection Level:** `{mode}`\n"
                f"• **Safe Mode:** `{safe}`\n"
                f"• **Security Alert Channel:** {alert_ch}\n\n"
                "**Available Modes:**\n"
                "🟢 `normal` — Standard monitoring & configured thresholds\n"
                "🟡 `enhanced` — Increased monitoring & 25% stricter anti-nuke thresholds\n"
                "🔴 `emergency` — Strictest thresholds + automatic Safe Mode + emergency alerts"
            ),
            color=THEME.PRIMARY,
        )
        await ctx.reply(embed=embed)

    @protection_group.command(
        name="mode",
        description="Switch server protection level between Normal, Enhanced, and Emergency Mode.",
    )
    @app_commands.describe(level="Protection level: normal, enhanced, or emergency")
    async def protection_mode_cmd(
        self, ctx: commands.Context[Any], level: str
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        lvl = level.lower().strip()
        if lvl not in ("normal", "enhanced", "emergency"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Mode", "Protection level must be `normal`, `enhanced`, or `emergency`."
                ),
                ephemeral=True,
            )
            return

        updates: dict[str, Any] = {"protection_mode": lvl}
        if lvl == "emergency":
            updates["safe_mode"] = 1
            updates["safe_mode_activated_by"] = ctx.author.id
            updates["safe_mode_activated_at"] = datetime.now(timezone.utc).isoformat()

        await self.bot.db.update_guild_config(ctx.guild.id, **updates)
        await self._notify_security_channel(
            ctx.guild,
            f"🛡️ Protection Mode Changed → {lvl.upper()}",
            f"Updated by {ctx.author.mention}."
            + (" Safe Mode was automatically engaged." if lvl == "emergency" else ""),
            color=THEME.DANGER if lvl == "emergency" else THEME.WARNING,
        )
        await ctx.reply(
            embed=success_embed(
                f"Protection Mode: {lvl.upper()}",
                f"Server protection posture is now set to **{lvl.upper()}**"
                + (" (Safe Mode automatically engaged)." if lvl == "emergency" else "."),
            )
        )

    @protection_group.command(
        name="alert_channel",
        description="Set the dedicated channel for security and anti-nuke alerts.",
    )
    @app_commands.describe(channel="Text channel to receive security incident alerts")
    async def protection_alert_channel(
        self, ctx: commands.Context[Any], channel: discord.TextChannel
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.update_guild_config(
            ctx.guild.id, security_alert_channel_id=channel.id
        )
        await ctx.reply(
            embed=success_embed(
                "Security Alert Channel Set",
                f"All anti-nuke and Safe Mode alerts will be sent to {channel.mention}.",
            )
        )

    @protection_group.command(
        name="lockdown",
        description="Emergency Server Lockdown: lock public text channels and activate Safe Mode.",
    )
    @app_commands.describe(reason="Reason for emergency server lockdown")
    async def protection_lockdown(
        self,
        ctx: commands.Context[Any],
        *,
        reason: str = "Emergency security lockdown",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        locked_count = 0
        for ch in ctx.guild.text_channels:
            perms = ch.overwrites_for(ctx.guild.default_role)
            if perms.send_messages is not False and ch.permissions_for(ctx.guild.default_role).view_channel:
                perms.send_messages = False
                try:
                    await ch.set_permissions(
                        ctx.guild.default_role,
                        overwrite=perms,
                        reason=f"[Lockdown by {ctx.author}] {reason}",
                    )
                    locked_count += 1
                except Exception:
                    continue

        await self.bot.db.update_guild_config(
            ctx.guild.id,
            protection_mode="emergency",
            safe_mode=1,
            safe_mode_activated_by=ctx.author.id,
            safe_mode_activated_at=datetime.now(timezone.utc).isoformat(),
        )
        await ctx.reply(
            embed=warning_embed(
                "🚨 Server Lockdown Engaged",
                f"Locked **{locked_count}** public text channels and engaged **Emergency Mode + Safe Mode**.\n"
                f"**Reason:** {reason}",
            )
        )

    @protection_group.command(
        name="unlockdown",
        description="Lift an emergency server channel lockdown and restore normal mode.",
    )
    async def protection_unlockdown(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        unlocked_count = 0
        for ch in ctx.guild.text_channels:
            perms = ch.overwrites_for(ctx.guild.default_role)
            if perms.send_messages is False:
                perms.send_messages = None
                try:
                    await ch.set_permissions(
                        ctx.guild.default_role,
                        overwrite=perms,
                        reason=f"Unlockdown by {ctx.author}",
                    )
                    unlocked_count += 1
                except Exception:
                    continue

        await self.bot.db.update_guild_config(
            ctx.guild.id, protection_mode="normal", safe_mode=0
        )
        await ctx.reply(
            embed=success_embed(
                "🔓 Server Lockdown Lifted",
                f"Restored send permissions in **{unlocked_count}** channels and returned to **Normal Mode**.",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(ProtectionCog(bot))
