"""
Cosmic ki baddie — Customizable Welcome & Goodbye System (`cogs/welcome.py`)
Features:
- Custom text & embed welcome messages with variable interpolation
  ({user.mention}, {user.name}, {user.avatar}, {server}, {member_count}, {user.created_at})
- Multiple welcome message templates (randomly selected on join if configured)
- Automatic role assignment on join (multiple auto-roles supported)
- Optional DM welcome message
- Customizable goodbye messages & embeds
"""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import embed_from_dict, error_embed, info_embed, make_embed, success_embed
from utils.helpers import render_template

if TYPE_CHECKING:
    from main import CosmicBot


class WelcomeCog(commands.Cog, name="Welcome"):
    """Welcome and goodbye message automation, templates, and auto-roles."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def _send_welcome(
        self, member: discord.Member, test_channel: Optional[discord.TextChannel] = None
    ) -> bool:
        cfg = await self.bot.db.ensure_welcome_config(member.guild.id)
        if not cfg.get("welcome_enabled") and test_channel is None:
            return False

        # Assign auto-roles on real join
        if test_channel is None:
            auto_role_ids = json.loads(cfg.get("welcome_auto_roles_json") or "[]")
            roles_to_add = [
                r
                for rid in auto_role_ids
                if (r := member.guild.get_role(int(rid))) is not None
                and (not member.guild.me or r < member.guild.me.top_role)
            ]
            if roles_to_add:
                try:
                    await member.add_roles(*roles_to_add, reason="Cosmic Welcome Auto-Role")
                except Exception:
                    pass

        channel = test_channel or (
            member.guild.get_channel(int(cfg["welcome_channel_id"]))
            if cfg.get("welcome_channel_id")
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            return False

        templates: List[str] = json.loads(cfg.get("welcome_templates_json") or "[]")
        raw_template = (
            random.choice(templates)
            if templates
            else str(
                cfg.get("welcome_message")
                or "Welcome to **{server}**, {user.mention}! You are member #{member_count}."
            )
        )
        content = render_template(raw_template, member=member, guild=member.guild)

        embed_obj: Optional[discord.Embed] = None
        if cfg.get("welcome_embed_json"):
            try:
                embed_data = json.loads(cfg["welcome_embed_json"])
                embed_obj = embed_from_dict(embed_data, member=member, guild=member.guild)
            except Exception:
                embed_obj = None
        else:
            embed_obj = make_embed(
                title=f"🌌 Welcome to {member.guild.name}!",
                description=content,
                color=THEME.PRIMARY,
                thumbnail_url=member.display_avatar.url if member.display_avatar else None,
            )
            embed_obj.add_field(
                name="Member Count",
                value=f"`#{member.guild.member_count or len(member.guild.members)}`",
                inline=True,
            )
            embed_obj.add_field(
                name="Account Created",
                value=discord.utils.format_dt(member.created_at, "R")
                if member.created_at
                else "Unknown",
                inline=True,
            )

        try:
            await channel.send(content=member.mention, embed=embed_obj)
        except Exception:
            return False

        if test_channel is None and cfg.get("welcome_dm_enabled") and cfg.get("welcome_dm_message"):
            try:
                dm_txt = render_template(
                    str(cfg["welcome_dm_message"]), member=member, guild=member.guild
                )
                await member.send(embed=info_embed(f"Welcome to {member.guild.name}!", dm_txt))
            except Exception:
                pass
        return True

    async def _send_goodbye(
        self, member: discord.Member, test_channel: Optional[discord.TextChannel] = None
    ) -> bool:
        cfg = await self.bot.db.ensure_welcome_config(member.guild.id)
        if not cfg.get("goodbye_enabled") and test_channel is None:
            return False

        channel = test_channel or (
            member.guild.get_channel(int(cfg["goodbye_channel_id"]))
            if cfg.get("goodbye_channel_id")
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            return False

        raw_msg = str(
            cfg.get("goodbye_message")
            or "**{user.name}** has left **{server}**. We now have {member_count} members."
        )
        content = render_template(raw_msg, member=member, guild=member.guild)
        embed_obj = make_embed(
            title="👋 Member Departed",
            description=content,
            color=THEME.SECONDARY,
            thumbnail_url=member.display_avatar.url if member.display_avatar else None,
        )
        try:
            await channel.send(embed=embed_obj)
            return True
        except Exception:
            return False

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not member.bot:
            await self._send_welcome(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not member.bot:
            await self._send_goodbye(member)

    # =========================================================================
    # /welcome & /goodbye Commands
    # =========================================================================
    @commands.hybrid_group(
        name="welcome",
        description="Configure welcome messages, embeds, templates, and automatic join roles.",
        invoke_without_command=True,
    )
    async def welcome_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_welcome_config(ctx.guild.id)
        auto_roles = json.loads(cfg.get("welcome_auto_roles_json") or "[]")
        templates = json.loads(cfg.get("welcome_templates_json") or "[]")
        embed = make_embed(
            title=f"👋 Welcome & Goodbye Settings — {ctx.guild.name}",
            color=THEME.PRIMARY,
        )
        embed.add_field(
            name="Welcome Status",
            value="✅ Enabled" if cfg.get("welcome_enabled") else "❌ Disabled",
            inline=True,
        )
        embed.add_field(
            name="Welcome Channel",
            value=f"<#{cfg['welcome_channel_id']}>" if cfg.get("welcome_channel_id") else "*Not set*",
            inline=True,
        )
        embed.add_field(
            name="Auto-Roles on Join",
            value=", ".join(f"<@&{r}>" for r in auto_roles) or "*None*",
            inline=False,
        )
        embed.add_field(
            name=f"Welcome Message / Templates ({len(templates) or 1})",
            value=cfg.get("welcome_message") or "Default",
            inline=False,
        )
        embed.add_field(
            name="Goodbye Status",
            value=(
                f"{'✅ Enabled' if cfg.get('goodbye_enabled') else '❌ Disabled'} "
                f"({('<#' + str(cfg['goodbye_channel_id']) + '>') if cfg.get('goodbye_channel_id') else '*No channel*'})"
            ),
            inline=False,
        )
        await ctx.reply(embed=embed)

    @welcome_group.command(
        name="channel", description="Set the welcome channel and enable the welcome system."
    )
    @app_commands.describe(channel="Channel for welcome messages", enabled="Enable or disable welcome messages")
    async def welcome_channel(
        self, ctx: commands.Context[Any], channel: discord.TextChannel, enabled: bool = True
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.ensure_welcome_config(ctx.guild.id)
        await self.bot.db.execute(
            "UPDATE welcome_configs SET welcome_channel_id = ?, welcome_enabled = ? WHERE guild_id = ?",
            (channel.id, int(enabled), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Welcome Channel Configured",
                f"Welcome messages are now **{'Enabled' if enabled else 'Disabled'}** in {channel.mention}.",
            )
        )

    @welcome_group.command(
        name="message",
        description="Customize the primary welcome message text (supports {user.mention}, {server}, {member_count}).",
    )
    @app_commands.describe(template="Welcome message template text")
    async def welcome_message(self, ctx: commands.Context[Any], *, template: str) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.ensure_welcome_config(ctx.guild.id)
        await self.bot.db.execute(
            "UPDATE welcome_configs SET welcome_message = ? WHERE guild_id = ?",
            (template, ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed("Welcome Message Updated", f"**New Template:**\n{template}")
        )

    @welcome_group.command(
        name="template_add",
        description="Add an additional welcome message template to the random rotation pool.",
    )
    @app_commands.describe(template="Welcome template text to add")
    async def welcome_template_add(
        self, ctx: commands.Context[Any], *, template: str
    ) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_welcome_config(ctx.guild.id)
        templates = json.loads(cfg.get("welcome_templates_json") or "[]")
        templates.append(template)
        await self.bot.db.execute(
            "UPDATE welcome_configs SET welcome_templates_json = ? WHERE guild_id = ?",
            (json.dumps(templates[:20]), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Welcome Template Added",
                f"Added template to rotation pool (**{len(templates)}** total templates).",
            )
        )

    @welcome_group.command(
        name="autorole",
        description="Add or remove an automatic role assigned to new members upon joining.",
    )
    @app_commands.describe(role="Role to toggle in the welcome auto-role list")
    async def welcome_autorole(
        self, ctx: commands.Context[Any], role: discord.Role
    ) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_welcome_config(ctx.guild.id)
        roles: List[int] = json.loads(cfg.get("welcome_auto_roles_json") or "[]")
        if role.id in roles:
            roles.remove(role.id)
            state = "Removed from"
        else:
            roles.append(role.id)
            state = "Added to"
        await self.bot.db.execute(
            "UPDATE welcome_configs SET welcome_auto_roles_json = ? WHERE guild_id = ?",
            (json.dumps(roles), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Welcome Auto-Roles Updated",
                f"{state} join auto-roles: {role.mention}.\n"
                f"Current auto-roles: {', '.join(f'<@&{r}>' for r in roles) or '*None*'}",
            )
        )

    @welcome_group.command(
        name="test", description="Send a live preview of the welcome message in this channel."
    )
    async def welcome_test(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        await self._send_welcome(ctx.author, test_channel=ctx.channel)
        await ctx.reply(
            embed=success_embed("Welcome Preview Sent", "Dispatched test welcome card above!"),
            ephemeral=True,
        )

    @commands.hybrid_group(
        name="goodbye",
        description="Configure server goodbye messages and channel.",
        invoke_without_command=True,
    )
    async def goodbye_group(self, ctx: commands.Context[Any]) -> None:
        await self.welcome_group(ctx)

    @goodbye_group.command(
        name="config",
        description="Configure the goodbye channel, message template, and enabled state.",
    )
    @app_commands.describe(
        channel="Channel to post goodbye messages",
        enabled="Enable or disable goodbye messages",
        message="Goodbye message template ({user.name}, {server}, {member_count})",
    )
    async def goodbye_config(
        self,
        ctx: commands.Context[Any],
        channel: discord.TextChannel,
        enabled: bool = True,
        *,
        message: str = "**{user.name}** has left **{server}**. We now have {member_count} members.",
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.ensure_welcome_config(ctx.guild.id)
        await self.bot.db.execute(
            """
            UPDATE welcome_configs
            SET goodbye_channel_id = ?, goodbye_enabled = ?, goodbye_message = ?
            WHERE guild_id = ?
            """,
            (channel.id, int(enabled), message, ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Goodbye System Configured",
                f"**Status:** {'Enabled' if enabled else 'Disabled'}\n"
                f"**Channel:** {channel.mention}\n"
                f"**Message:** {message}",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(WelcomeCog(bot))
