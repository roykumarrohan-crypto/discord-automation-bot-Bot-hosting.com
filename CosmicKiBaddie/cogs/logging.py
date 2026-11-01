"""
Cosmic ki baddie — Comprehensive Server Logging System (`cogs/logging.py`)
Tracks 19 distinct server, member, message, voice, moderation, invite, ticket,
and configuration events with category-specific channel routing, customizable
embed styles, audit-log correlation, and event deduplication.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed
from utils.helpers import format_duration, parse_hex_color

if TYPE_CHECKING:
    from main import CosmicBot

ALL_LOG_EVENTS: Tuple[str, ...] = (
    "message_delete",
    "message_edit",
    "member_join",
    "member_leave",
    "member_ban",
    "member_unban",
    "role_create",
    "role_delete",
    "role_update",
    "channel_create",
    "channel_delete",
    "channel_update",
    "nickname_change",
    "voice_activity",
    "member_timeout",
    "moderation_action",
    "invite_change",
    "ticket_activity",
    "config_change",
)

EVENT_TO_CHANNEL_COLUMN: Dict[str, str] = {
    "message_delete": "message_log_channel_id",
    "message_edit": "message_log_channel_id",
    "member_join": "member_log_channel_id",
    "member_leave": "member_log_channel_id",
    "nickname_change": "member_log_channel_id",
    "member_timeout": "moderation_log_channel_id",
    "member_ban": "moderation_log_channel_id",
    "member_unban": "moderation_log_channel_id",
    "moderation_action": "moderation_log_channel_id",
    "role_create": "server_log_channel_id",
    "role_delete": "server_log_channel_id",
    "role_update": "server_log_channel_id",
    "channel_create": "server_log_channel_id",
    "channel_delete": "server_log_channel_id",
    "channel_update": "server_log_channel_id",
    "voice_activity": "voice_log_channel_id",
    "invite_change": "invite_log_channel_id",
    "ticket_activity": "ticket_log_channel_id",
    "config_change": "config_log_channel_id",
}


class LoggingCog(commands.Cog, name="Logging"):
    """Multi-channel server event logger with audit-log integration and deduplication."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot
        self._dedup_cache: Dict[str, float] = {}

    def _is_duplicate(self, key: str, ttl: float = 4.0) -> bool:
        """Prevent duplicate event log emissions within `ttl` seconds."""
        now = time.monotonic()
        # Prune expired keys occasionally
        if len(self._dedup_cache) > 500:
            expired = [k for k, ts in self._dedup_cache.items() if now - ts > ttl]
            for k in expired:
                self._dedup_cache.pop(k, None)
        last = self._dedup_cache.get(key)
        if last is not None and (now - last) < ttl:
            return True
        self._dedup_cache[key] = now
        return False

    async def _resolve_log_channel(
        self, guild: discord.Guild, event_key: str
    ) -> Optional[discord.TextChannel]:
        """Resolve the target text channel if the event is enabled for this guild."""
        log_cfg = await self.bot.db.ensure_logging_config(guild.id)
        enabled_map = json.loads(log_cfg.get("enabled_events_json") or "{}")
        if not enabled_map.get(event_key, True):
            return None

        col = EVENT_TO_CHANNEL_COLUMN.get(event_key, "default_log_channel_id")
        channel_id = log_cfg.get(col) or log_cfg.get("default_log_channel_id")
        if not channel_id:
            guild_cfg = await self.bot.db.ensure_guild(guild.id)
            channel_id = guild_cfg.get("modlog_channel_id")
        if not channel_id:
            return None

        ch = guild.get_channel(int(channel_id))
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _dispatch_log(
        self,
        guild: discord.Guild,
        event_key: str,
        title: str,
        description: str,
        fields: Optional[list[tuple[str, str, bool]]] = None,
        color: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        dedup_token: Optional[str] = None,
    ) -> Optional[int]:
        """Format according to the guild's configured embed style and send to the log channel."""
        if dedup_token and self._is_duplicate(f"{guild.id}:{event_key}:{dedup_token}"):
            return None

        channel = await self._resolve_log_channel(guild, event_key)
        if channel is None:
            return None

        log_cfg = await self.bot.db.ensure_logging_config(guild.id)
        style = (log_cfg.get("embed_style") or "detailed").lower()
        base_color = color or int(log_cfg.get("embed_color") or THEME.PRIMARY)

        if style == "minimal":
            embed = make_embed(
                description=f"**{title}** — {description}",
                color=base_color,
                timestamp=True,
            )
        else:
            embed = make_embed(
                title=title,
                description=description,
                color=base_color,
                thumbnail_url=thumbnail_url if style == "detailed" else None,
            )
            if fields and style == "detailed":
                for fname, fval, finline in fields[:20]:
                    embed.add_field(name=fname, value=fval[:1024], inline=finline)

        try:
            msg = await channel.send(embed=embed)
            return msg.id
        except Exception:
            return None

    async def _fetch_audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: Optional[int] = None,
    ) -> Optional[discord.abc.User]:
        """Safely inspect recent audit log entries to identify the responsible actor."""
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age > 15:
                    continue
                if target_id is None or getattr(entry.target, "id", None) == target_id:
                    return entry.user
        except Exception:
            return None
        return None

    # =========================================================================
    # Public Helpers Called by Other Cogs (Moderation, Tickets, Config)
    # =========================================================================
    async def log_moderation_case(
        self, guild: discord.Guild, case_row: Dict[str, Any]
    ) -> Optional[int]:
        """Log a moderation case to the moderation log channel."""
        fields = [
            ("Case ID", f"`#{case_row['case_number']}`", True),
            ("Action", f"`{case_row['action'].upper()}`", True),
            ("Target", f"<@{case_row['target_id']}> (`{case_row['target_id']}`)", True),
            ("Moderator", f"<@{case_row['moderator_id']}>", True),
        ]
        if case_row.get("duration_seconds"):
            fields.append(("Duration", format_duration(case_row["duration_seconds"]), True))
        fields.append(("Reason", str(case_row.get("reason") or "None"), False))
        return await self._dispatch_log(
            guild=guild,
            event_key="moderation_action",
            title=f"🛡️ Moderation Case #{case_row['case_number']} — {case_row['action'].upper()}",
            description=f"<@{case_row['moderator_id']}> executed **{case_row['action']}** on <@{case_row['target_id']}>.",
            fields=fields,
            color=THEME.WARNING,
            dedup_token=f"case:{case_row['case_number']}",
        )

    async def log_ticket_event(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        action_title: str,
        details: str,
    ) -> None:
        """Log ticket lifecycle events (created, claimed, closed, reopened, deleted)."""
        await self._dispatch_log(
            guild=guild,
            event_key="ticket_activity",
            title=f"🎫 Ticket Activity — {action_title}",
            description=details,
            fields=[("Actor", f"{actor.mention} (`{actor.id}`)", True)],
            color=THEME.ACCENT,
        )

    async def log_config_change(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        setting_name: str,
        summary: str,
    ) -> None:
        """Log bot configuration updates made inside Discord."""
        await self._dispatch_log(
            guild=guild,
            event_key="config_change",
            title=f"⚙️ Configuration Updated — {setting_name}",
            description=summary,
            fields=[("Administrator", f"{actor.mention} (`{actor.id}`)", True)],
            color=THEME.PRIMARY,
        )

    # =========================================================================
    # Discord Event Listeners (19 Tracked Event Types)
    # =========================================================================
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        attachments = [a.url for a in message.attachments]
        self.bot.snipe_cache[message.channel.id] = {
            "content": message.content,
            "author_name": str(message.author),
            "author_id": message.author.id,
            "author_avatar": message.author.display_avatar.url if message.author.display_avatar else None,
            "attachments": attachments,
            "deleted_at": discord.utils.format_dt(datetime.now(timezone.utc), "R"),
        }
        fields = [
            ("Author", f"{message.author.mention} (`{message.author.id}`)", True),
            ("Channel", f"<#{message.channel.id}>", True),
        ]
        if attachments:
            fields.append(("Attachments", "\n".join(attachments[:5]), False))
        await self._dispatch_log(
            guild=message.guild,
            event_key="message_delete",
            title="🗑️ Message Deleted",
            description=message.content[:1500] if message.content else "*(No text content)*",
            fields=fields,
            color=THEME.DANGER,
            dedup_token=f"msgdel:{message.id}",
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.guild is None or before.author.bot or before.content == after.content:
            return
        self.bot.editsnipe_cache[before.channel.id] = {
            "before": before.content,
            "after": after.content,
            "author_name": str(before.author),
            "author_id": before.author.id,
            "author_avatar": before.author.display_avatar.url if before.author.display_avatar else None,
            "jump_url": after.jump_url,
        }
        await self._dispatch_log(
            guild=before.guild,
            event_key="message_edit",
            title="✏️ Message Edited",
            description=f"[Jump to Message]({after.jump_url}) in <#{before.channel.id}>",
            fields=[
                ("Author", f"{before.author.mention} (`{before.author.id}`)", True),
                ("Before", (before.content or "*Empty*")[:1000], False),
                ("After", (after.content or "*Empty*")[:1000], False),
            ],
            color=THEME.INFO,
            dedup_token=f"msgedit:{after.id}:{hash(after.content)}",
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        age = discord.utils.format_dt(member.created_at, "R") if member.created_at else "Unknown"
        await self._dispatch_log(
            guild=member.guild,
            event_key="member_join",
            title="📥 Member Joined",
            description=f"{member.mention} (`{member.id}`) joined **{member.guild.name}**.",
            fields=[
                ("Account Created", age, True),
                ("Member Count", f"`{member.guild.member_count or len(member.guild.members)}`", True),
            ],
            color=THEME.SUCCESS,
            thumbnail_url=member.display_avatar.url if member.display_avatar else None,
            dedup_token=f"join:{member.id}",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        roles = [r.mention for r in member.roles if r.id != member.guild.default_role.id]
        await self._dispatch_log(
            guild=member.guild,
            event_key="member_leave",
            title="📤 Member Left",
            description=f"**{member}** (`{member.id}`) left **{member.guild.name}**.",
            fields=[("Roles", ", ".join(roles[:15]) or "None", False)],
            color=THEME.WARNING,
            thumbnail_url=member.display_avatar.url if member.display_avatar else None,
            dedup_token=f"leave:{member.id}",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = await self._fetch_audit_actor(guild, discord.AuditLogAction.ban, user.id)
        await self._dispatch_log(
            guild=guild,
            event_key="member_ban",
            title="🔨 Member Banned",
            description=f"**{user}** (`{user.id}`) was banned from the server.",
            fields=[("Responsible Moderator", f"{actor.mention if actor else 'Unknown'}", True)],
            color=THEME.DANGER,
            dedup_token=f"ban:{user.id}",
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = await self._fetch_audit_actor(guild, discord.AuditLogAction.unban, user.id)
        await self._dispatch_log(
            guild=guild,
            event_key="member_unban",
            title="🔓 Member Unbanned",
            description=f"**{user}** (`{user.id}`) was unbanned.",
            fields=[("Responsible Moderator", f"{actor.mention if actor else 'Unknown'}", True)],
            color=THEME.SUCCESS,
            dedup_token=f"unban:{user.id}",
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        actor = await self._fetch_audit_actor(role.guild, discord.AuditLogAction.role_create, role.id)
        await self._dispatch_log(
            guild=role.guild,
            event_key="role_create",
            title="🎭 Role Created",
            description=f"Role {role.mention} (`{role.name}`) was created.",
            fields=[
                ("Role ID", f"`{role.id}`", True),
                ("Created By", f"{actor.mention if actor else 'Unknown'}", True),
            ],
            color=THEME.SUCCESS,
            dedup_token=f"rolecreate:{role.id}",
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor = await self._fetch_audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self._dispatch_log(
            guild=role.guild,
            event_key="role_delete",
            title="🗑️ Role Deleted",
            description=f"Role **`@{role.name}`** (`{role.id}`) was deleted.",
            fields=[("Deleted By", f"{actor.mention if actor else 'Unknown'}", True)],
            color=THEME.DANGER,
            dedup_token=f"roledel:{role.id}",
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"**Color:** `{before.color}` → `{after.color}`")
        if before.permissions != after.permissions:
            changes.append("**Permissions:** Modified")
        if not changes:
            return
        await self._dispatch_log(
            guild=after.guild,
            event_key="role_update",
            title="🎭 Role Updated",
            description=f"Role {after.mention} (`{after.id}`) was updated.\n" + "\n".join(changes),
            color=THEME.INFO,
            dedup_token=f"roleupd:{after.id}:{hash(tuple(changes))}",
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._fetch_audit_actor(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await self._dispatch_log(
            guild=channel.guild,
            event_key="channel_create",
            title="📁 Channel Created",
            description=f"Channel <#{channel.id}> (`{channel.name}`) was created.",
            fields=[("Created By", f"{actor.mention if actor else 'Unknown'}", True)],
            color=THEME.SUCCESS,
            dedup_token=f"chcreate:{channel.id}",
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._fetch_audit_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await self._dispatch_log(
            guild=channel.guild,
            event_key="channel_delete",
            title="🗑️ Channel Deleted",
            description=f"Channel **`#{channel.name}`** (`{channel.id}`) was deleted.",
            fields=[("Deleted By", f"{actor.mention if actor else 'Unknown'}", True)],
            color=THEME.DANGER,
            dedup_token=f"chdel:{channel.id}",
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"**Name:** `{before.name}` → `{after.name}`")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append("**Topic:** Updated")
        if getattr(before, "slowmode_delay", 0) != getattr(after, "slowmode_delay", 0):
            changes.append(
                f"**Slowmode:** `{getattr(before, 'slowmode_delay', 0)}s` → `{getattr(after, 'slowmode_delay', 0)}s`"
            )
        if not changes:
            return
        await self._dispatch_log(
            guild=after.guild,
            event_key="channel_update",
            title="📝 Channel Updated",
            description=f"Channel <#{after.id}> was modified:\n" + "\n".join(changes),
            color=THEME.INFO,
            dedup_token=f"chupd:{after.id}:{hash(tuple(changes))}",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # 1. Nickname change
        if before.nick != after.nick:
            await self._dispatch_log(
                guild=after.guild,
                event_key="nickname_change",
                title="🏷️ Nickname Changed",
                description=f"{after.mention} (`{after.id}`) changed nickname.",
                fields=[
                    ("Before", f"`{before.nick or before.name}`", True),
                    ("After", f"`{after.nick or after.name}`", True),
                ],
                color=THEME.INFO,
                dedup_token=f"nick:{after.id}:{after.nick}",
            )
        # 2. Timeout event
        before_to = getattr(before, "timed_out_until", None)
        after_to = getattr(after, "timed_out_until", None)
        if before_to != after_to:
            if after_to and after_to > datetime.now(timezone.utc):
                await self._dispatch_log(
                    guild=after.guild,
                    event_key="member_timeout",
                    title="⏳ Member Timed Out",
                    description=f"{after.mention} was timed out until {discord.utils.format_dt(after_to, 'f')}.",
                    color=THEME.WARNING,
                    dedup_token=f"timeout:{after.id}:{after_to.isoformat()}",
                )
            elif before_to and not after_to:
                await self._dispatch_log(
                    guild=after.guild,
                    event_key="member_timeout",
                    title="🔊 Member Timeout Removed",
                    description=f"Timeout removed for {after.mention}.",
                    color=THEME.SUCCESS,
                    dedup_token=f"untimeout:{after.id}",
                )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel is None and after.channel is not None:
            desc = f"{member.mention} joined voice channel <#{after.channel.id}>."
            token = f"vjoin:{member.id}:{after.channel.id}"
        elif before.channel is not None and after.channel is None:
            desc = f"{member.mention} left voice channel <#{before.channel.id}>."
            token = f"vleave:{member.id}:{before.channel.id}"
        elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
            desc = f"{member.mention} moved from <#{before.channel.id}> to <#{after.channel.id}>."
            token = f"vmove:{member.id}:{after.channel.id}"
        else:
            return

        await self._dispatch_log(
            guild=member.guild,
            event_key="voice_activity",
            title="🎙️ Voice Channel Activity",
            description=desc,
            color=THEME.ACCENT,
            dedup_token=token,
        )

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if not isinstance(invite.guild, discord.Guild):
            return
        await self._dispatch_log(
            guild=invite.guild,
            event_key="invite_change",
            title="🔗 Invite Created",
            description=f"Invite `{invite.code}` created for <#{invite.channel.id if invite.channel else 0}>.",
            fields=[
                ("Inviter", f"{invite.inviter.mention if invite.inviter else 'Unknown'}", True),
                ("Max Uses", f"`{invite.max_uses or 'Unlimited'}`", True),
            ],
            color=THEME.INFO,
            dedup_token=f"invcreate:{invite.code}",
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if not isinstance(invite.guild, discord.Guild):
            return
        await self._dispatch_log(
            guild=invite.guild,
            event_key="invite_change",
            title="🗑️ Invite Deleted",
            description=f"Invite `{invite.code}` was deleted.",
            color=THEME.WARNING,
            dedup_token=f"invdel:{invite.code}",
        )

    # =========================================================================
    # /logging Configuration Commands
    # =========================================================================
    @commands.hybrid_group(
        name="logging",
        description="Configure server event logging channels, styles, and toggles.",
        invoke_without_command=True,
    )
    async def logging_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_logging_config(ctx.guild.id)
        enabled_map = json.loads(cfg.get("enabled_events_json") or "{}")
        enabled_list = [k for k in ALL_LOG_EVENTS if enabled_map.get(k, True)]
        disabled_list = [k for k in ALL_LOG_EVENTS if not enabled_map.get(k, True)]

        embed = make_embed(
            title=f"📡 Logging Configuration — {ctx.guild.name}",
            description=f"**Style:** `{cfg.get('embed_style', 'detailed')}` • **Color:** `#{int(cfg.get('embed_color', THEME.PRIMARY)):06X}`",
            color=int(cfg.get("embed_color") or THEME.PRIMARY),
        )
        ch_summary = (
            f"• **Default:** {('<#' + str(cfg['default_log_channel_id']) + '>') if cfg.get('default_log_channel_id') else '*Unset*'}\n"
            f"• **Moderation:** {('<#' + str(cfg['moderation_log_channel_id']) + '>') if cfg.get('moderation_log_channel_id') else '*Fallback*'}\n"
            f"• **Messages:** {('<#' + str(cfg['message_log_channel_id']) + '>') if cfg.get('message_log_channel_id') else '*Fallback*'}\n"
            f"• **Members:** {('<#' + str(cfg['member_log_channel_id']) + '>') if cfg.get('member_log_channel_id') else '*Fallback*'}\n"
            f"• **Server/Roles:** {('<#' + str(cfg['server_log_channel_id']) + '>') if cfg.get('server_log_channel_id') else '*Fallback*'}\n"
            f"• **Voice:** {('<#' + str(cfg['voice_log_channel_id']) + '>') if cfg.get('voice_log_channel_id') else '*Fallback*'}\n"
            f"• **Tickets:** {('<#' + str(cfg['ticket_log_channel_id']) + '>') if cfg.get('ticket_log_channel_id') else '*Fallback*'}\n"
            f"• **Config:** {('<#' + str(cfg['config_log_channel_id']) + '>') if cfg.get('config_log_channel_id') else '*Fallback*'}\n"
            f"• **Invites:** {('<#' + str(cfg['invite_log_channel_id']) + '>') if cfg.get('invite_log_channel_id') else '*Fallback*'}"
        )
        embed.add_field(name="Logging Channels", value=ch_summary, inline=False)
        embed.add_field(
            name=f"Enabled Events ({len(enabled_list)}/{len(ALL_LOG_EVENTS)})",
            value=", ".join(f"`{e}`" for e in enabled_list) or "None",
            inline=False,
        )
        if disabled_list:
            embed.add_field(
                name="Disabled Events",
                value=", ".join(f"`{e}`" for e in disabled_list),
                inline=False,
            )
        await ctx.reply(embed=embed)

    @logging_group.command(
        name="channel",
        description="Assign a text channel to a specific logging category.",
    )
    @app_commands.describe(
        category="Category: default, moderation, message, member, server, voice, ticket, config, invite",
        channel="Text channel to receive logs for this category",
    )
    async def logging_channel(
        self,
        ctx: commands.Context[Any],
        category: str,
        channel: discord.TextChannel,
    ) -> None:
        if ctx.guild is None:
            return
        cat_map = {
            "default": "default_log_channel_id",
            "moderation": "moderation_log_channel_id",
            "message": "message_log_channel_id",
            "member": "member_log_channel_id",
            "server": "server_log_channel_id",
            "voice": "voice_log_channel_id",
            "ticket": "ticket_log_channel_id",
            "config": "config_log_channel_id",
            "invite": "invite_log_channel_id",
        }
        col = cat_map.get(category.lower().strip())
        if not col:
            await ctx.reply(
                embed=error_embed(
                    "Invalid Category",
                    f"Choose one of: {', '.join(f'`{k}`' for k in cat_map.keys())}",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.ensure_logging_config(ctx.guild.id)
        await self.bot.db.execute(
            f"UPDATE logging_configs SET {col} = ? WHERE guild_id = ?",
            (channel.id, ctx.guild.id),
        )
        if col in ("default_log_channel_id", "moderation_log_channel_id"):
            await self.bot.db.update_guild_config(ctx.guild.id, modlog_channel_id=channel.id)
        await ctx.reply(
            embed=success_embed(
                "Logging Channel Updated",
                f"**{category.title()}** logs will now be sent to {channel.mention}.",
            )
        )

    @logging_group.command(
        name="toggle", description="Enable or disable an individual logging event."
    )
    @app_commands.describe(
        event_name="Event key (e.g. message_delete, member_join, voice_activity) or 'all'",
        enabled="True to enable, False to disable",
    )
    async def logging_toggle(
        self, ctx: commands.Context[Any], event_name: str, enabled: bool
    ) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_logging_config(ctx.guild.id)
        enabled_map = json.loads(cfg.get("enabled_events_json") or "{}")
        ev = event_name.lower().strip()
        if ev == "all":
            for k in ALL_LOG_EVENTS:
                enabled_map[k] = enabled
        elif ev in ALL_LOG_EVENTS:
            enabled_map[ev] = enabled
        else:
            await ctx.reply(
                embed=error_embed(
                    "Unknown Event",
                    f"Available events: {', '.join(f'`{k}`' for k in ALL_LOG_EVENTS)}",
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.execute(
            "UPDATE logging_configs SET enabled_events_json = ? WHERE guild_id = ?",
            (json.dumps(enabled_map), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Logging Event Updated",
                f"Logging event `{ev}` is now **{'ENABLED ✅' if enabled else 'DISABLED ❌'}**.",
            )
        )

    @logging_group.command(
        name="style",
        description="Customize the embed visual style and hex color for server logs.",
    )
    @app_commands.describe(
        style="Embed style: detailed, compact, or minimal",
        hex_color="Hex color code (e.g. #8B5CF6)",
    )
    async def logging_style(
        self,
        ctx: commands.Context[Any],
        style: str = "detailed",
        hex_color: str = "#8B5CF6",
    ) -> None:
        if ctx.guild is None:
            return
        st = style.lower().strip()
        if st not in ("detailed", "compact", "minimal"):
            await ctx.reply(
                embed=error_embed("Invalid Style", "Choose `detailed`, `compact`, or `minimal`."),
                ephemeral=True,
            )
            return
        color_val = parse_hex_color(hex_color, THEME.PRIMARY)
        await self.bot.db.ensure_logging_config(ctx.guild.id)
        await self.bot.db.execute(
            "UPDATE logging_configs SET embed_style = ?, embed_color = ? WHERE guild_id = ?",
            (st, color_val, ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Logging Style Updated",
                f"Log embeds now use **{st}** style with color `#{color_val:06X}`.",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(LoggingCog(bot))
