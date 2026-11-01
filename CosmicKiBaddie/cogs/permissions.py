"""
Cosmic ki baddie — Advanced Role Permission Manager (`cogs/permissions.py`)
Provides complete control over which roles and users can execute which bot commands:
/permissions panel
/permissions set
/permissions remove
/permissions view
/permissions reset
/permissions tier
Includes interactive Discord dropdowns, buttons, and modal inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed

if TYPE_CHECKING:
    from main import CosmicBot


class PermissionRuleModal(discord.ui.Modal, title="Add Command Permission Rule"):
    """Modal to bind a selected role to a specific command or category with allow/deny effect."""

    command_input = discord.ui.TextInput(
        label="Command Name (or category:<name> or *)",
        placeholder="e.g. ban, giveaway create, category:moderation, *",
        required=True,
        max_length=100,
    )
    effect_input = discord.ui.TextInput(
        label="Effect (allow or deny)",
        default="allow",
        required=True,
        max_length=10,
    )

    def __init__(
        self,
        panel_view: "PermissionsPanelView",
        role_id: int,
    ) -> None:
        super().__init__()
        self.panel_view = panel_view
        self.role_id = role_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cmd_name = self.command_input.value.strip().lower()
        effect = self.effect_input.value.strip().lower()
        if effect not in ("allow", "deny"):
            await interaction.response.send_message(
                embed=error_embed("Invalid Effect", "Effect must be `allow` or `deny`."),
                ephemeral=True,
            )
            return

        await self.panel_view.bot.db.set_permission_rule(
            guild_id=self.panel_view.guild.id,
            target_type="role",
            target_id=self.role_id,
            command_name=cmd_name,
            effect=effect,
            created_by=interaction.user.id,
        )
        await interaction.response.edit_message(
            embed=await self.panel_view.build_embed(), view=self.panel_view
        )


class PermissionsPanelView(discord.ui.View):
    """Interactive Discord control center for managing role tiers and command permissions."""

    def __init__(self, bot: "CosmicBot", guild: discord.Guild) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.guild = guild
        self.selected_tier: str = "admin_role_id"
        self.selected_role_id: Optional[int] = None

    async def build_embed(self) -> discord.Embed:
        cfg = await self.bot.db.ensure_guild(self.guild.id)
        rules = await self.bot.db.get_permission_rules(self.guild.id)

        def _fmt_role(col: str) -> str:
            rid = cfg.get(col)
            return f"<@&{rid}>" if rid else "*Not set (uses Discord permissions)*"

        embed = make_embed(
            title=f"🔐 Interactive Permission Manager — {self.guild.name}",
            description=(
                "Configure Server Role Tiers or attach granular **Allow / Deny** overrides "
                "for individual commands and categories.\n"
                "*Server Owner & Discord Administrators always retain emergency recovery access.*"
            ),
            color=THEME.PRIMARY,
        )
        tiers_text = (
            f"• **Admin Role (Full Bot Management):** {_fmt_role('admin_role_id')}\n"
            f"• **Moderator Role (Moderation Suite):** {_fmt_role('mod_role_id')}\n"
            f"• **Support Role (Ticket Management):** {_fmt_role('support_role_id')}\n"
            f"• **Event Manager Role (Giveaways & Events):** {_fmt_role('event_manager_role_id')}\n"
            f"• **Member Role (Basic Interactive Commands):** {_fmt_role('member_role_id')}"
        )
        embed.add_field(name="🏛️ Core Role Tiers", value=tiers_text, inline=False)

        if rules:
            rule_lines = [
                f"• `{r['command_name']}` → **{r['effect'].upper()}** "
                f"('<@&' + str(r['target_id']) + '>' if r['target_type'] == 'role' else '<@' + str(r['target_id']) + '>')"
                for r in rules[:15]
            ]
            if len(rules) > 15:
                rule_lines.append(f"*...and {len(rules) - 15} more rule(s).*")
            embed.add_field(
                name=f"📜 Granular Command Rules ({len(rules)})",
                value="\n".join(rule_lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="📜 Granular Command Rules",
                value="*No custom overrides set. Commands follow Core Role Tiers above.*",
                inline=False,
            )
        return embed

    @discord.ui.select(
        placeholder="1. Choose a Core Role Tier to assign...",
        options=[
            discord.SelectOption(
                label="Admin Role (Full Bot Management)",
                value="admin_role_id",
                emoji="👑",
            ),
            discord.SelectOption(
                label="Moderator Role (Moderation Commands)",
                value="mod_role_id",
                emoji="🛡️",
            ),
            discord.SelectOption(
                label="Support Role (Ticket Management)",
                value="support_role_id",
                emoji="🎫",
            ),
            discord.SelectOption(
                label="Event Manager Role (Giveaways & Events)",
                value="event_manager_role_id",
                emoji="🎉",
            ),
            discord.SelectOption(
                label="Member Role (Standard Commands)",
                value="member_role_id",
                emoji="🌟",
            ),
        ],
        row=0,
    )
    async def select_tier(
        self, interaction: discord.Interaction, select: discord.ui.Select[Any]
    ) -> None:
        self.selected_tier = select.values[0]
        await interaction.response.send_message(
            f"Selected tier `{self.selected_tier}`. Now pick a role in the dropdown below!",
            ephemeral=True,
        )

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="2. Pick a Server Role (for Tier or Command Rule)...",
        min_values=1,
        max_values=1,
        row=1,
    )
    async def select_role(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect[Any]
    ) -> None:
        role = select.values[0]
        self.selected_role_id = role.id
        await self.bot.db.update_guild_config(
            self.guild.id, **{self.selected_tier: role.id}
        )
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(
        label="Add Command Rule for Selected Role",
        style=discord.ButtonStyle.primary,
        emoji="➕",
        row=2,
    )
    async def add_rule_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        if not self.selected_role_id:
            await interaction.response.send_message(
                embed=error_embed("Select a Role First", "Please pick a role in Dropdown #2 first."),
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            PermissionRuleModal(self, self.selected_role_id)
        )

    @discord.ui.button(
        label="Reset All Custom Rules",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        row=2,
    )
    async def reset_rules_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.bot.db.reset_permission_rules(self.guild.id)
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)


class PermissionsCog(commands.Cog, name="Permissions"):
    """Granular role & user command permission manager."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    @commands.hybrid_group(
        name="permissions",
        description="Configure which roles and members can access specific bot commands and features.",
        invoke_without_command=True,
    )
    async def permissions_group(self, ctx: commands.Context[Any]) -> None:
        await self.permissions_panel(ctx)

    @permissions_group.command(
        name="panel",
        description="Open the interactive Discord Permission Manager panel.",
    )
    async def permissions_panel(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        view = PermissionsPanelView(self.bot, ctx.guild)
        embed = await view.build_embed()
        await ctx.reply(embed=embed, view=view)

    @permissions_group.command(
        name="tier",
        description="Assign a server role to one of the 5 core bot permission tiers.",
    )
    @app_commands.describe(
        tier="Tier: admin, moderator, support, event, or member",
        role="The Discord Role to assign to this tier",
    )
    async def permissions_tier(
        self, ctx: commands.Context[Any], tier: str, role: discord.Role
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        tier_map = {
            "admin": "admin_role_id",
            "administrator": "admin_role_id",
            "mod": "mod_role_id",
            "moderator": "mod_role_id",
            "support": "support_role_id",
            "event": "event_manager_role_id",
            "events": "event_manager_role_id",
            "member": "member_role_id",
        }
        col = tier_map.get(tier.lower().strip())
        if not col:
            await ctx.reply(
                embed=error_embed(
                    "Invalid Tier",
                    "Valid tiers: `admin`, `moderator`, `support`, `event`, `member`.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild_config(ctx.guild.id, **{col: role.id})
        await ctx.reply(
            embed=success_embed(
                "Permission Tier Updated",
                f"Assigned {role.mention} as the server's **{tier.title()}** role.",
            )
        )

    @permissions_group.command(
        name="set",
        description="Grant or deny a role access to a specific command, category:<name>, or *.",
    )
    @app_commands.describe(
        role="Target role to authorize or restrict",
        command_name="Command name (e.g. 'ban', 'giveaway create', 'category:moderation', '*')",
        effect="Rule effect: 'allow' or 'deny'",
    )
    async def permissions_set(
        self,
        ctx: commands.Context[Any],
        role: discord.Role,
        command_name: str,
        effect: str = "allow",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        eff = effect.lower().strip()
        if eff not in ("allow", "deny"):
            await ctx.reply(
                embed=error_embed("Invalid Effect", "Effect must be `allow` or `deny`."),
                ephemeral=True,
            )
            return
        cmd_key = command_name.lower().strip()
        await self.bot.db.set_permission_rule(
            guild_id=ctx.guild.id,
            target_type="role",
            target_id=role.id,
            command_name=cmd_key,
            effect=eff,
            created_by=ctx.author.id,
        )
        await ctx.reply(
            embed=success_embed(
                "Permission Rule Saved",
                f"Role {role.mention} is now **{eff.upper()}ED** on `{cmd_key}`.",
            )
        )

    @permissions_group.command(
        name="remove",
        description="Remove a command permission rule for a role.",
    )
    @app_commands.describe(role="Target role", command_name="Command name to remove rule from")
    async def permissions_remove(
        self, ctx: commands.Context[Any], role: discord.Role, *, command_name: str
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.remove_permission_rule(
            ctx.guild.id, "role", role.id, command_name
        )
        await ctx.reply(
            embed=success_embed(
                "Permission Rule Removed",
                f"Removed permission rule for {role.mention} on `{command_name.lower().strip()}`.",
            )
        )

    @permissions_group.command(
        name="view",
        description="View all configured role tiers and custom command permission rules.",
    )
    async def permissions_view(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        view = PermissionsPanelView(self.bot, ctx.guild)
        await ctx.reply(embed=await view.build_embed())

    @permissions_group.command(
        name="reset",
        description="Reset all custom command permission rules back to default server tiers.",
    )
    async def permissions_reset(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.reset_permission_rules(ctx.guild.id)
        await ctx.reply(
            embed=success_embed(
                "Permissions Reset",
                "All custom command permission overrides have been cleared.",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(PermissionsCog(bot))
