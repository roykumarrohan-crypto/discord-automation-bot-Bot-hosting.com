"""
Cosmic ki baddie — Interactive Command Management System (`cogs/command_manager.py`)
Manage all bot commands directly inside Discord without editing source code:
/command list
/command enable
/command disable
/command permissions
/command cooldown
/command settings
Supports category toggles, channel allow/deny lists, role restrictions, and custom aliases.
Never allows disabling essential security or emergency recovery commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed

if TYPE_CHECKING:
    from main import CosmicBot


class AliasModal(discord.ui.Modal, title="Configure Command Aliases"):
    """Modal for configuring custom prefix aliases for any command."""

    aliases_input = discord.ui.TextInput(
        label="Comma-separated aliases (e.g. b, hammer)",
        placeholder="Leave blank to clear custom aliases",
        required=False,
        max_length=200,
    )

    def __init__(self, bot: "CosmicBot", guild_id: int, command_name: str) -> None:
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.command_name = command_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.aliases_input.value or ""
        aliases = [a.strip().lower() for a in raw.split(",") if a.strip()][:10]
        setting = await self.bot.db.upsert_command_setting(
            self.guild_id, self.command_name, aliases=aliases
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Command Aliases Updated",
                f"Custom aliases for `{self.command_name}`: "
                + (", ".join(f"`{a}`" for a in setting["aliases"]) or "*None*"),
            ),
            ephemeral=True,
        )


class CommandSettingsView(discord.ui.View):
    """Interactive Discord UI panel for configuring a command's channels, roles, aliases, and state."""

    def __init__(self, bot: "CosmicBot", guild: discord.Guild, command_name: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.command_name = command_name.lower().strip()

    async def build_embed(self) -> discord.Embed:
        s = await self.bot.db.get_command_setting(self.guild.id, self.command_name)
        allowed_roles = ", ".join(f"<@&{r}>" for r in s["allowed_roles"]) or "Default Tier"
        denied_roles = ", ".join(f"<@&{r}>" for r in s["denied_roles"]) or "None"
        allowed_chs = ", ".join(f"<#{c}>" for c in s["allowed_channels"]) or "All Channels"
        denied_chs = ", ".join(f"<#{c}>" for c in s["denied_channels"]) or "None"
        aliases = ", ".join(f"`{a}`" for a in s["aliases"]) or "None"

        embed = make_embed(
            title=f"⚙️ Command Settings — `{self.command_name}`",
            description=(
                "Use the controls below to customize this command in real time.\n"
                "All changes persist across restarts immediately."
            ),
            color=THEME.PRIMARY,
        )
        embed.add_field(
            name="Status",
            value="✅ Enabled" if s["enabled"] else "❌ Disabled",
            inline=True,
        )
        embed.add_field(
            name="Category Status",
            value="✅ Active" if s["category_enabled"] else "❌ Disabled",
            inline=True,
        )
        embed.add_field(
            name="Cooldown",
            value=f"`{s['cooldown_seconds']}s`",
            inline=True,
        )
        embed.add_field(name="Allowed Roles", value=allowed_roles, inline=False)
        embed.add_field(name="Denied Roles", value=denied_roles, inline=False)
        embed.add_field(name="Allowed Channels", value=allowed_chs, inline=False)
        embed.add_field(name="Denied Channels", value=denied_chs, inline=False)
        embed.add_field(name="Custom Prefix Aliases", value=aliases, inline=False)
        return embed

    @discord.ui.button(label="Toggle Enable/Disable", style=discord.ButtonStyle.primary, row=0)
    async def toggle_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        if self.command_name in CONFIG.protected_commands:
            await interaction.response.send_message(
                embed=error_embed(
                    "Protected Command",
                    f"`{self.command_name}` is a protected recovery command and cannot be disabled.",
                ),
                ephemeral=True,
            )
            return
        cur = await self.bot.db.get_command_setting(self.guild.id, self.command_name)
        await self.bot.db.upsert_command_setting(
            self.guild.id, self.command_name, enabled=not cur["enabled"]
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Set Aliases", style=discord.ButtonStyle.secondary, row=0)
    async def aliases_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(
            AliasModal(self.bot, self.guild.id, self.command_name)
        )

    @discord.ui.button(label="Clear Restrictions", style=discord.ButtonStyle.danger, row=0)
    async def reset_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.bot.db.upsert_command_setting(
            self.guild.id,
            self.command_name,
            enabled=True,
            category_enabled=True,
            cooldown_seconds=0,
            allowed_roles=[],
            denied_roles=[],
            allowed_channels=[],
            denied_channels=[],
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Select Allowed Roles for this command...",
        min_values=0,
        max_values=10,
        row=1,
    )
    async def select_allowed_roles(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect[Any]
    ) -> None:
        role_ids = [r.id for r in select.values]
        await self.bot.db.upsert_command_setting(
            self.guild.id, self.command_name, allowed_roles=role_ids
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Select Allowed Channels (leave empty for all)...",
        min_values=0,
        max_values=10,
        row=2,
    )
    async def select_allowed_channels(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect[Any]
    ) -> None:
        ch_ids = [c.id for c in select.values]
        await self.bot.db.upsert_command_setting(
            self.guild.id, self.command_name, allowed_channels=ch_ids
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


class CommandManagerCog(commands.Cog, name="CommandManager"):
    """Manage command state, cooldowns, channels, roles, and categories inside Discord."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    def _resolve_command(self, name: str) -> Optional[commands.Command[Any, ..., Any]]:
        return self.bot.get_command(name.lower().strip())

    @commands.hybrid_group(
        name="command",
        description="Manage bot commands, categories, cooldowns, and permissions without editing code.",
        invoke_without_command=True,
    )
    async def command_group(self, ctx: commands.Context[Any]) -> None:
        await self.command_list(ctx)

    @command_group.command(
        name="list",
        description="View all available bot commands, their categories, and enabled status.",
    )
    @app_commands.describe(category="Optional category name to filter by")
    async def command_list(
        self, ctx: commands.Context[Any], category: Optional[str] = None
    ) -> None:
        if ctx.guild is None:
            return
        embed = make_embed(
            title=f"🧭 {CONFIG.bot_name} — Command Registry",
            description="Use `/command settings <name>` to customize any command interactively.",
            color=THEME.PRIMARY,
        )
        for cog_name, cog in sorted(self.bot.cogs.items()):
            if category and category.lower() not in cog_name.lower():
                continue
            cmds = cog.get_commands()
            if not cmds:
                continue
            items = []
            for c in cmds:
                st = await self.bot.db.get_command_setting(ctx.guild.id, c.qualified_name)
                icon = "✅" if st["enabled"] and st["category_enabled"] else "❌"
                items.append(f"{icon} `{c.qualified_name}`")
            embed.add_field(
                name=f"📂 {cog_name} ({len(items)})",
                value=", ".join(items)[:1024],
                inline=False,
            )
        await ctx.reply(embed=embed)

    @command_group.command(
        name="enable",
        description="Enable a specific command or an entire command category.",
    )
    @app_commands.describe(target="Command name or 'category:<CogName>'")
    async def command_enable(self, ctx: commands.Context[Any], *, target: str) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        key = target.strip()
        if key.lower().startswith("category:"):
            cat_name = key.split(":", 1)[1].strip()
            matched_cog = next(
                (c for name, c in self.bot.cogs.items() if name.lower() == cat_name.lower()),
                None,
            )
            if not matched_cog:
                await ctx.reply(
                    embed=error_embed("Category Not Found", f"No category named `{cat_name}`."),
                    ephemeral=True,
                )
                return
            for cmd in matched_cog.walk_commands():
                await self.bot.db.upsert_command_setting(
                    ctx.guild.id, cmd.qualified_name, category_enabled=True, enabled=True
                )
            await ctx.reply(
                embed=success_embed(
                    "Category Enabled",
                    f"All commands in category **{matched_cog.qualified_name}** are now enabled.",
                )
            )
            return

        cmd = self._resolve_command(key)
        if not cmd:
            await ctx.reply(
                embed=error_embed("Command Not Found", f"No command matching `{key}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.upsert_command_setting(
            ctx.guild.id, cmd.qualified_name, enabled=True, category_enabled=True
        )
        await ctx.reply(
            embed=success_embed(
                "Command Enabled", f"Command `{cmd.qualified_name}` is now **enabled**."
            )
        )

    @command_group.command(
        name="disable",
        description="Disable a specific command or command category (protected commands are safeguarded).",
    )
    @app_commands.describe(target="Command name or 'category:<CogName>'")
    async def command_disable(self, ctx: commands.Context[Any], *, target: str) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        key = target.strip().lower()
        if key in CONFIG.protected_commands:
            await ctx.reply(
                embed=error_embed(
                    "Safety Lock",
                    f"🛡️ `{key}` is an essential management/recovery command and cannot be disabled.",
                ),
                ephemeral=True,
            )
            return

        if key.startswith("category:"):
            cat_name = key.split(":", 1)[1].strip()
            if cat_name in ("commandmanager", "permissions", "protection", "antinuke", "help"):
                await ctx.reply(
                    embed=error_embed(
                        "Safety Lock",
                        f"🛡️ Category `{cat_name}` contains essential security/management functions and cannot be disabled.",
                    ),
                    ephemeral=True,
                )
                return
            matched_cog = next(
                (c for name, c in self.bot.cogs.items() if name.lower() == cat_name.lower()),
                None,
            )
            if not matched_cog:
                await ctx.reply(
                    embed=error_embed("Category Not Found", f"No category named `{cat_name}`."),
                    ephemeral=True,
                )
                return
            for cmd in matched_cog.walk_commands():
                if cmd.qualified_name.lower() not in CONFIG.protected_commands:
                    await self.bot.db.upsert_command_setting(
                        ctx.guild.id, cmd.qualified_name, category_enabled=False
                    )
            await ctx.reply(
                embed=success_embed(
                    "Category Disabled",
                    f"Commands in category **{matched_cog.qualified_name}** have been disabled.",
                )
            )
            return

        cmd = self._resolve_command(key)
        if not cmd:
            await ctx.reply(
                embed=error_embed("Command Not Found", f"No command matching `{key}`."),
                ephemeral=True,
            )
            return
        if cmd.qualified_name.lower() in CONFIG.protected_commands:
            await ctx.reply(
                embed=error_embed(
                    "Safety Lock",
                    f"🛡️ `{cmd.qualified_name}` cannot be disabled.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.upsert_command_setting(
            ctx.guild.id, cmd.qualified_name, enabled=False
        )
        await ctx.reply(
            embed=success_embed(
                "Command Disabled", f"Command `{cmd.qualified_name}` is now **disabled**."
            )
        )

    @command_group.command(
        name="cooldown",
        description="Configure a per-user cooldown in seconds for any command.",
    )
    @app_commands.describe(
        command_name="Target command name (e.g. ban, snipe, giveaway create)",
        seconds="Cooldown in seconds (0 to disable)",
    )
    async def command_cooldown(
        self, ctx: commands.Context[Any], command_name: str, seconds: int
    ) -> None:
        if ctx.guild is None:
            return
        cmd = self._resolve_command(command_name)
        if not cmd:
            await ctx.reply(
                embed=error_embed("Command Not Found", f"No command matching `{command_name}`."),
                ephemeral=True,
            )
            return
        cd = max(0, min(86400, seconds))
        await self.bot.db.upsert_command_setting(
            ctx.guild.id, cmd.qualified_name, cooldown_seconds=cd
        )
        await ctx.reply(
            embed=success_embed(
                "Command Cooldown Updated",
                f"Cooldown for `{cmd.qualified_name}` is now **{cd}s**.",
            )
        )

    @command_group.command(
        name="permissions",
        description="View or assign allowed roles and channels for a specific command.",
    )
    @app_commands.describe(
        command_name="Command name to inspect or update",
        allowed_role="Optional role to grant access to this command",
        allowed_channel="Optional channel to restrict this command to",
    )
    async def command_permissions(
        self,
        ctx: commands.Context[Any],
        command_name: str,
        allowed_role: Optional[discord.Role] = None,
        allowed_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        cmd = self._resolve_command(command_name)
        if not cmd:
            await ctx.reply(
                embed=error_embed("Command Not Found", f"No command matching `{command_name}`."),
                ephemeral=True,
            )
            return

        st = await self.bot.db.get_command_setting(ctx.guild.id, cmd.qualified_name)
        if allowed_role:
            roles = list(set(st["allowed_roles"] + [allowed_role.id]))
            st = await self.bot.db.upsert_command_setting(
                ctx.guild.id, cmd.qualified_name, allowed_roles=roles
            )
            await self.bot.db.set_permission_rule(
                ctx.guild.id, "role", allowed_role.id, cmd.qualified_name, "allow", ctx.author.id
            )
        if allowed_channel:
            chs = list(set(st["allowed_channels"] + [allowed_channel.id]))
            st = await self.bot.db.upsert_command_setting(
                ctx.guild.id, cmd.qualified_name, allowed_channels=chs
            )

        rules = await self.bot.db.get_permission_rules(ctx.guild.id, cmd.qualified_name)
        rule_lines = [
            f"• **{r['effect'].upper()}** {r['target_type']} "
            f"('<@&' + str(r['target_id']) + '>' if r['target_type'] == 'role' else '<@' + str(r['target_id']) + '>')"
            for r in rules
        ]
        embed = make_embed(
            title=f"🔐 Command Permissions — `{cmd.qualified_name}`",
            description=cmd.description or "No description.",
            color=THEME.PRIMARY,
        )
        embed.add_field(
            name="Allowed Roles",
            value=", ".join(f"<@&{r}>" for r in st["allowed_roles"]) or "Default Tier",
            inline=True,
        )
        embed.add_field(
            name="Allowed Channels",
            value=", ".join(f"<#{c}>" for c in st["allowed_channels"]) or "All Channels",
            inline=True,
        )
        embed.add_field(
            name="Explicit Permission Rules",
            value="\n".join(rule_lines) or "None (uses default role tier)",
            inline=False,
        )
        await ctx.reply(embed=embed)

    @command_group.command(
        name="settings",
        description="Open an interactive control panel for a specific command.",
    )
    @app_commands.describe(command_name="Name of the command to configure")
    async def command_settings(
        self, ctx: commands.Context[Any], *, command_name: str
    ) -> None:
        if ctx.guild is None:
            return
        cmd = self._resolve_command(command_name)
        if not cmd:
            await ctx.reply(
                embed=error_embed("Command Not Found", f"No command matching `{command_name}`."),
                ephemeral=True,
            )
            return
        view = CommandSettingsView(self.bot, ctx.guild, cmd.qualified_name)
        embed = await view.build_embed()
        await ctx.reply(embed=embed, view=view)


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(CommandManagerCog(bot))
