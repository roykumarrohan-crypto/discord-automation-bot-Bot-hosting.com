"""
Cosmic ki baddie — Dynamic Interactive Help System (`cogs/help.py`)
Commands:
/help
/help moderation
/help automation
/help tickets
/help roles
/help security
/help events
/help configuration
Automatically reflects live command enabled/disabled states, custom prefixes,
cooldowns, and permissions. Includes interactive dropdown navigation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import info_embed, make_embed
from utils.permissions import CATEGORY_DEFAULT_TIER

if TYPE_CHECKING:
    from main import CosmicBot

HELP_TOPIC_TO_COGS: Dict[str, List[str]] = {
    "overview": [],
    "moderation": ["Moderation", "Logging"],
    "automation": ["Automation", "Autoresponder", "Welcome"],
    "tickets": ["Tickets", "Embeds"],
    "roles": ["Roles", "Permissions"],
    "security": ["AntiNuke", "Protection"],
    "events": ["Giveaways", "Events", "Polls"],
    "configuration": ["SetupWizard", "CommandManager", "Permissions", "Logging"],
}


class HelpCategorySelect(discord.ui.Select[Any]):
    """Dropdown selector allowing users to browse help categories interactively."""

    def __init__(self, help_cog: "HelpCog", guild: Optional[discord.Guild]) -> None:
        self.help_cog = help_cog
        self.guild = guild
        options = [
            discord.SelectOption(
                label="Overview & Quick Start",
                value="overview",
                emoji="🌌",
                description="Bot overview, prefixes, and module directory",
            ),
            discord.SelectOption(
                label="Moderation & Logging",
                value="moderation",
                emoji="🛡️",
                description="25 moderation commands, cases, escalation & 19 log trackers",
            ),
            discord.SelectOption(
                label="Automation, Activity & Autoresponders",
                value="automation",
                emoji="⚙️",
                description="XP/levels, scheduled messages, keyword triggers & welcome",
            ),
            discord.SelectOption(
                label="Tickets & Embed Studio",
                value="tickets",
                emoji="🎫",
                description="Support ticket panels, transcripts & interactive embed builder",
            ),
            discord.SelectOption(
                label="Roles & Permission Manager",
                value="roles",
                emoji="🎭",
                description="Role CRUD, persistent role menus, temp roles & permissions",
            ),
            discord.SelectOption(
                label="Security, Anti-Nuke & Safe Mode",
                value="security",
                emoji="🚨",
                description="Anti-nuke detectors, Safe Mode & server protection levels",
            ),
            discord.SelectOption(
                label="Giveaways, Events & Polls",
                value="events",
                emoji="🎉",
                description="Community giveaways, event RSVPs & interactive polls",
            ),
            discord.SelectOption(
                label="Configuration & Command Manager",
                value="configuration",
                emoji="🪄",
                description="/setup wizard, /command manager & /prefix configuration",
            ),
        ]
        super().__init__(
            placeholder="Browse Cosmic ki baddie command categories...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        topic = self.values[0]
        embed = await self.help_cog.build_topic_embed(interaction.guild, topic)
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpBrowserView(discord.ui.View):
    """Interactive View wrapping the HelpCategorySelect dropdown."""

    def __init__(self, help_cog: "HelpCog", guild: Optional[discord.Guild]) -> None:
        super().__init__(timeout=300)
        self.add_item(HelpCategorySelect(help_cog, guild))


class HelpCog(commands.Cog, name="Help"):
    """Dynamic interactive help menu that automatically updates when commands change state."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def build_topic_embed(
        self, guild: Optional[discord.Guild], topic: str
    ) -> discord.Embed:
        prefixes = (
            await self.bot.db.get_prefixes(guild.id, CONFIG.default_prefix)
            if guild
            else [CONFIG.default_prefix]
        )
        primary_prefix = prefixes[0]
        t_key = topic.lower().strip()

        if t_key == "overview" or t_key not in HELP_TOPIC_TO_COGS:
            embed = make_embed(
                title=f"🌌 {CONFIG.bot_name} — Interactive Help Center",
                description=(
                    f"Welcome to **{CONFIG.bot_name}**, the all-in-one server management system for **{CONFIG.community_name}**!\n\n"
                    f"• **Slash Commands:** `/help`, `/setup`, `/permissions panel`, etc.\n"
                    f"• **Active Prefix(es):** {', '.join(f'`{p}`' for p in prefixes)} (e.g. `{primary_prefix}help`)\n"
                    f"• **Total Loaded Cogs:** `{len(self.bot.cogs)}` | **Registered Commands:** `{len(self.bot.commands)}`"
                ),
                color=THEME.PRIMARY,
            )
            embed.add_field(
                name="🛡️ `/help moderation`",
                value="25 moderation commands, cases, warning escalation & 19 server log events",
                inline=True,
            )
            embed.add_field(
                name="⚙️ `/help automation`",
                value="Activity XP/levels, role rewards, scheduled messages, autoresponders & welcome",
                inline=True,
            )
            embed.add_field(
                name="🎫 `/help tickets`",
                value="Multi-category ticket panels, transcripts & interactive Embed Studio",
                inline=True,
            )
            embed.add_field(
                name="🎭 `/help roles`",
                value="Role CRUD, temp roles, persistent button role panels & permission manager",
                inline=True,
            )
            embed.add_field(
                name="🚨 `/help security`",
                value="Anti-Nuke rate detectors, Safe Mode lockdown & 3 Protection Modes",
                inline=True,
            )
            embed.add_field(
                name="🎉 `/help events`",
                value="Giveaways, community event RSVPs & anonymous/public polls",
                inline=True,
            )
            embed.add_field(
                name="🪄 `/help configuration`",
                value="Interactive `/setup` wizard, `/command` manager & `/prefix` settings",
                inline=False,
            )
            return embed

        target_cogs = HELP_TOPIC_TO_COGS[t_key]
        embed = make_embed(
            title=f"📖 Help Category — {t_key.title()}",
            description=(
                f"Both Slash (`/`) and Prefix (`{primary_prefix}`) invocations are supported.\n"
                f"Status icons reflect real-time server command state (`✅` Enabled / `❌` Disabled)."
            ),
            color=THEME.PRIMARY,
        )

        for cog_name in target_cogs:
            cog = self.bot.get_cog(cog_name)
            if cog is None:
                continue
            tier = CATEGORY_DEFAULT_TIER.get(cog_name.lower(), "member").upper()
            cmd_lines: List[str] = []
            for cmd in cog.get_commands():
                st = (
                    await self.bot.db.get_command_setting(guild.id, cmd.qualified_name)
                    if guild
                    else {"enabled": True, "category_enabled": True, "cooldown_seconds": 0}
                )
                is_on = st["enabled"] and st["category_enabled"]
                icon = "✅" if is_on else "❌"
                sub_names = ""
                if isinstance(cmd, commands.Group):
                    subs = [sc.name for sc in cmd.commands]
                    if subs:
                        sub_names = f" *({', '.join(subs[:8])})*"
                cd_note = f" `[CD: {st['cooldown_seconds']}s]`" if st.get("cooldown_seconds") else ""
                cmd_lines.append(
                    f"{icon} **`/{cmd.qualified_name}`**{sub_names}{cd_note} — {cmd.description or 'Command group'}"
                )

            if cmd_lines:
                embed.add_field(
                    name=f"📂 {cog_name} (Default Access: {tier})",
                    value="\n".join(cmd_lines)[:1024],
                    inline=False,
                )

        if t_key == "configuration":
            embed.add_field(
                name="🔑 Prefix Commands (`/prefix`)",
                value="✅ **`/prefix view`**, **`/prefix set`**, **`/prefix add`**, **`/prefix remove`** — Manage custom server prefixes.",
                inline=False,
            )
        return embed

    @commands.hybrid_group(
        name="help",
        description="Open the interactive Cosmic ki baddie Help Center.",
        invoke_without_command=True,
    )
    async def help_group(
        self, ctx: commands.Context[Any], *, category: Optional[str] = None
    ) -> None:
        topic = (category or "overview").lower().strip()
        embed = await self.build_topic_embed(ctx.guild, topic)
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="moderation", description="View help for Moderation & Logging commands."
    )
    async def help_moderation(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "moderation")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="automation",
        description="View help for Automation, Activity XP, Autoresponders & Welcome commands.",
    )
    async def help_automation(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "automation")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="tickets", description="View help for Ticket Management & Embed Studio commands."
    )
    async def help_tickets(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "tickets")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="roles", description="View help for Role Management & Permission Manager commands."
    )
    async def help_roles(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "roles")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="security",
        description="View help for Anti-Nuke, Safe Mode & Server Protection commands.",
    )
    async def help_security(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "security")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="events", description="View help for Giveaways, Events & Polls commands."
    )
    async def help_events(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "events")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))

    @help_group.command(
        name="configuration",
        description="View help for the Setup Wizard, Command Manager & Prefix commands.",
    )
    async def help_configuration(self, ctx: commands.Context[Any]) -> None:
        embed = await self.build_topic_embed(ctx.guild, "configuration")
        await ctx.reply(embed=embed, view=HelpBrowserView(self, ctx.guild))


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(HelpCog(bot))
