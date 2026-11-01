"""
Cosmic ki baddie — Interactive Multi-Page Setup Wizard (`cogs/setup_wizard.py`)
Command: /setup
Guides server administrators through:
1. Channels & Ticket Category (Moderation log, Welcome, Goodbye, Ticket category, Automation channel)
2. Core Roles (Administrator, Moderator, Support, Event Manager)
3. Prefix, Welcome & Autoresponder Quick Configuration (via Modal)
4. Activity Role Rewards, Anti-Nuke Protection & Command Permissions
5. Full Configuration Review Summary with Save & Cancel
Does not require every setting to be filled out immediately; saves incrementally or on confirmation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

import discord
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed

if TYPE_CHECKING:
    from main import CosmicBot


class WizardCustomizeModal(discord.ui.Modal, title="Customize Prefix, Welcome & Autoresponder"):
    """Modal inside the /setup wizard for setting prefix, welcome text, and a starter autoresponder."""

    prefix_input = discord.ui.TextInput(
        label="Command Prefix(es) (comma-separated)",
        default="!",
        required=False,
        max_length=30,
    )
    welcome_input = discord.ui.TextInput(
        label="Welcome Message Template",
        style=discord.TextStyle.paragraph,
        default="Welcome to **{server}**, {user.mention}! You are member #{member_count}.",
        required=False,
        max_length=1000,
    )
    autoresponder_trigger = discord.ui.TextInput(
        label="Optional Starter Autoresponder Keyword",
        placeholder="e.g. help|support",
        required=False,
        max_length=100,
    )
    autoresponder_reply = discord.ui.TextInput(
        label="Optional Starter Autoresponder Reply",
        placeholder="e.g. Open a ticket in #support!",
        required=False,
        max_length=500,
    )

    def __init__(self, wizard: "SetupWizardView") -> None:
        super().__init__()
        self.wizard = wizard
        self.prefix_input.default = ", ".join(wizard.draft.get("prefixes", ["!"]))
        self.welcome_input.default = str(
            wizard.draft.get(
                "welcome_message",
                "Welcome to **{server}**, {user.mention}! You are member #{member_count}.",
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_p = self.prefix_input.value or "!"
        prefixes = [p.strip() for p in raw_p.split(",") if p.strip()] or ["!"]
        self.wizard.draft["prefixes"] = prefixes
        if self.welcome_input.value:
            self.wizard.draft["welcome_message"] = self.welcome_input.value.strip()
        if self.autoresponder_trigger.value and self.autoresponder_reply.value:
            self.wizard.draft["starter_ar_trigger"] = self.autoresponder_trigger.value.strip()
            self.wizard.draft["starter_ar_reply"] = self.autoresponder_reply.value.strip()
        await interaction.response.edit_message(
            embed=self.wizard.build_step_embed(),
            view=self.wizard,
        )


class SetupWizardView(discord.ui.View):
    """5-step interactive configuration wizard with Previous/Next navigation and live summary."""

    TOTAL_STEPS = 5

    def __init__(
        self,
        bot: "CosmicBot",
        guild: discord.Guild,
        author_id: int,
        initial_draft: Dict[str, Any],
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.step = 1
        self.draft = initial_draft
        self._active_channel_target = "modlog_channel_id"
        self._active_role_target = "admin_role_id"
        self._rebuild_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("Wizard Locked", "Only the administrator who launched `/setup` can use these controls."),
                ephemeral=True,
            )
            return False
        return True

    def _fmt_ch(self, key: str) -> str:
        val = self.draft.get(key)
        return f"<#{val}>" if val else "*Unchanged / Not set*"

    def _fmt_role(self, key: str) -> str:
        val = self.draft.get(key)
        return f"<@&{val}>" if val else "*Unchanged / Not set*"

    def build_step_embed(self) -> discord.Embed:
        step_titles = {
            1: "Step 1/5 — Channels & Ticket Category",
            2: "Step 2/5 — Staff & Permission Roles",
            3: "Step 3/5 — Prefix, Welcome & Autoresponder",
            4: "Step 4/5 — Activity Rewards & Anti-Nuke Security",
            5: "Step 5/5 — Review Configuration & Save",
        }
        embed = make_embed(
            title=f"🪄 Cosmic ki baddie Setup Wizard — {step_titles[self.step]}",
            color=THEME.PRIMARY,
        )

        if self.step == 1:
            embed.description = (
                "Select which channel setting to configure in Dropdown #1, then pick the channel in Dropdown #2.\n"
                "*All fields are optional — skip any setting you want to configure later!*"
            )
            embed.add_field(
                name="📡 Configured Channels",
                value=(
                    f"• **Moderation & Default Log Channel:** {self._fmt_ch('modlog_channel_id')}\n"
                    f"• **Welcome Channel:** {self._fmt_ch('welcome_channel_id')}\n"
                    f"• **Goodbye Channel:** {self._fmt_ch('goodbye_channel_id')}\n"
                    f"• **Ticket Category:** {self._fmt_ch('ticket_category_id')}\n"
                    f"• **Automation / Level-Up Channel:** {self._fmt_ch('level_up_channel_id')}"
                ),
                inline=False,
            )
        elif self.step == 2:
            embed.description = (
                "Assign your server's core staff and member permission roles.\n"
                "Choose the role tier in Dropdown #1, then select the Discord Role in Dropdown #2."
            )
            embed.add_field(
                name="🎭 Configured Role Tiers",
                value=(
                    f"• **Administrator Role:** {self._fmt_role('admin_role_id')}\n"
                    f"• **Moderator Role:** {self._fmt_role('mod_role_id')}\n"
                    f"• **Support Role (Tickets):** {self._fmt_role('support_role_id')}\n"
                    f"• **Event Manager Role:** {self._fmt_role('event_manager_role_id')}\n"
                    f"• **Welcome Auto-Role:** {self._fmt_role('welcome_auto_role_id')}"
                ),
                inline=False,
            )
        elif self.step == 3:
            embed.description = (
                "Click **📝 Customize Prefix, Welcome & Autoresponder** below to open the input modal."
            )
            embed.add_field(
                name="⚙️ Current Draft Values",
                value=(
                    f"• **Custom Prefix(es):** {', '.join(f'`{p}`' for p in self.draft.get('prefixes', ['!']))}\n"
                    f"• **Welcome System:** `{'Enabled' if self.draft.get('welcome_enabled') else 'Ready'}`\n"
                    f"• **Welcome Template:** {self.draft.get('welcome_message')}\n"
                    f"• **Starter Autoresponder:** "
                    + (
                        f"`{self.draft['starter_ar_trigger']}` → `{self.draft['starter_ar_reply']}`"
                        if self.draft.get("starter_ar_trigger")
                        else "*None added*"
                    )
                ),
                inline=False,
            )
        elif self.step == 4:
            embed.description = (
                "Configure activity progression, level-based role rewards (Level 5 reward role), "
                "and Anti-Nuke server protection posture."
            )
            embed.add_field(
                name="🛡️ Security & Progression Settings",
                value=(
                    f"• **Activity XP & Leveling:** `{'Enabled ✅' if self.draft.get('xp_enabled', True) else 'Disabled ❌'}`\n"
                    f"• **Level 5 Milestone Role Reward:** {self._fmt_role('level_5_role_id')}\n"
                    f"• **Anti-Nuke Protection:** `{'Enabled ✅' if self.draft.get('antinuke_enabled', True) else 'Disabled ❌'}`\n"
                    f"• **Server Protection Mode:** `{str(self.draft.get('protection_mode', 'normal')).upper()}`"
                ),
                inline=False,
            )
        else:
            embed.description = (
                "Review all your **Side Quest Studio** settings below.\n"
                "Click **💾 Save & Apply Configuration** to persist everything to the database immediately!"
            )
            embed.add_field(
                name="📡 Channels",
                value=(
                    f"Mod Log: {self._fmt_ch('modlog_channel_id')}\n"
                    f"Welcome: {self._fmt_ch('welcome_channel_id')}\n"
                    f"Goodbye: {self._fmt_ch('goodbye_channel_id')}\n"
                    f"Ticket Category: {self._fmt_ch('ticket_category_id')}\n"
                    f"Automation/Level-Up: {self._fmt_ch('level_up_channel_id')}"
                ),
                inline=True,
            )
            embed.add_field(
                name="🎭 Staff & Auto Roles",
                value=(
                    f"Admin: {self._fmt_role('admin_role_id')}\n"
                    f"Moderator: {self._fmt_role('mod_role_id')}\n"
                    f"Support: {self._fmt_role('support_role_id')}\n"
                    f"Events: {self._fmt_role('event_manager_role_id')}\n"
                    f"Level 5 Reward: {self._fmt_role('level_5_role_id')}"
                ),
                inline=True,
            )
            embed.add_field(
                name="⚡ Prefixes, Security & Automation",
                value=(
                    f"Prefixes: {', '.join(f'`{p}`' for p in self.draft.get('prefixes', ['!']))}\n"
                    f"Protection Mode: `{str(self.draft.get('protection_mode', 'normal')).upper()}`\n"
                    f"Anti-Nuke: `{'Enabled' if self.draft.get('antinuke_enabled', True) else 'Disabled'}`\n"
                    f"Activity XP: `{'Enabled' if self.draft.get('xp_enabled', True) else 'Disabled'}`"
                ),
                inline=False,
            )
        return embed

    def _rebuild_components(self) -> None:
        self.clear_items()

        if self.step == 1:
            ch_target_select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="1. Choose which channel setting to assign...",
                options=[
                    discord.SelectOption(
                        label="Moderation & Default Log Channel",
                        value="modlog_channel_id",
                        emoji="🛡️",
                        default=self._active_channel_target == "modlog_channel_id",
                    ),
                    discord.SelectOption(
                        label="Welcome Channel",
                        value="welcome_channel_id",
                        emoji="👋",
                        default=self._active_channel_target == "welcome_channel_id",
                    ),
                    discord.SelectOption(
                        label="Goodbye Channel",
                        value="goodbye_channel_id",
                        emoji="📤",
                        default=self._active_channel_target == "goodbye_channel_id",
                    ),
                    discord.SelectOption(
                        label="Ticket Category",
                        value="ticket_category_id",
                        emoji="🎫",
                        default=self._active_channel_target == "ticket_category_id",
                    ),
                    discord.SelectOption(
                        label="Automation / Level-Up Channel",
                        value="level_up_channel_id",
                        emoji="🎉",
                        default=self._active_channel_target == "level_up_channel_id",
                    ),
                ],
                row=0,
            )

            async def _on_ch_target(interaction: discord.Interaction) -> None:
                self._active_channel_target = ch_target_select.values[0]
                self._rebuild_components()
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            ch_target_select.callback = _on_ch_target
            self.add_item(ch_target_select)

            ch_types = (
                [discord.ChannelType.category]
                if self._active_channel_target == "ticket_category_id"
                else [discord.ChannelType.text]
            )
            ch_picker: discord.ui.ChannelSelect[Any] = discord.ui.ChannelSelect(
                channel_types=ch_types,
                placeholder="2. Pick the Channel / Category from your server...",
                min_values=1,
                max_values=1,
                row=1,
            )

            async def _on_ch_pick(interaction: discord.Interaction) -> None:
                picked = ch_picker.values[0]
                self.draft[self._active_channel_target] = picked.id
                if self._active_channel_target == "welcome_channel_id":
                    self.draft["welcome_enabled"] = True
                if self._active_channel_target == "goodbye_channel_id":
                    self.draft["goodbye_enabled"] = True
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            ch_picker.callback = _on_ch_pick
            self.add_item(ch_picker)

        elif self.step == 2:
            role_target_select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="1. Choose which Role Tier to assign...",
                options=[
                    discord.SelectOption(
                        label="Administrator Role",
                        value="admin_role_id",
                        emoji="👑",
                        default=self._active_role_target == "admin_role_id",
                    ),
                    discord.SelectOption(
                        label="Moderator Role",
                        value="mod_role_id",
                        emoji="🛡️",
                        default=self._active_role_target == "mod_role_id",
                    ),
                    discord.SelectOption(
                        label="Support Role (Tickets)",
                        value="support_role_id",
                        emoji="🎫",
                        default=self._active_role_target == "support_role_id",
                    ),
                    discord.SelectOption(
                        label="Event Manager Role",
                        value="event_manager_role_id",
                        emoji="🎉",
                        default=self._active_role_target == "event_manager_role_id",
                    ),
                    discord.SelectOption(
                        label="Welcome Auto-Role (On Join)",
                        value="welcome_auto_role_id",
                        emoji="🌟",
                        default=self._active_role_target == "welcome_auto_role_id",
                    ),
                ],
                row=0,
            )

            async def _on_role_target(interaction: discord.Interaction) -> None:
                self._active_role_target = role_target_select.values[0]
                self._rebuild_components()
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            role_target_select.callback = _on_role_target
            self.add_item(role_target_select)

            role_picker: discord.ui.RoleSelect[Any] = discord.ui.RoleSelect(
                placeholder="2. Select the Discord Role...",
                min_values=1,
                max_values=1,
                row=1,
            )

            async def _on_role_pick(interaction: discord.Interaction) -> None:
                self.draft[self._active_role_target] = role_picker.values[0].id
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            role_picker.callback = _on_role_pick
            self.add_item(role_picker)

        elif self.step == 3:
            modal_btn: discord.ui.Button[Any] = discord.ui.Button(
                label="Customize Prefix, Welcome & Autoresponder",
                style=discord.ButtonStyle.primary,
                emoji="📝",
                row=0,
            )

            async def _open_modal(interaction: discord.Interaction) -> None:
                await interaction.response.send_modal(WizardCustomizeModal(self))

            modal_btn.callback = _open_modal
            self.add_item(modal_btn)

        elif self.step == 4:
            mode_select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder="Select Server Protection Mode...",
                options=[
                    discord.SelectOption(label="Normal Protection Mode", value="normal", emoji="🟢"),
                    discord.SelectOption(label="Enhanced Protection Mode", value="enhanced", emoji="🟡"),
                    discord.SelectOption(label="Emergency Protection Mode", value="emergency", emoji="🔴"),
                ],
                row=0,
            )

            async def _on_mode(interaction: discord.Interaction) -> None:
                self.draft["protection_mode"] = mode_select.values[0]
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            mode_select.callback = _on_mode
            self.add_item(mode_select)

            reward_role_picker: discord.ui.RoleSelect[Any] = discord.ui.RoleSelect(
                placeholder="Optional: Select Level 5 Activity Reward Role...",
                min_values=1,
                max_values=1,
                row=1,
            )

            async def _on_reward_role(interaction: discord.Interaction) -> None:
                self.draft["level_5_role_id"] = reward_role_picker.values[0].id
                await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

            reward_role_picker.callback = _on_reward_role
            self.add_item(reward_role_picker)

        # Navigation Row (Row 3)
        prev_btn: discord.ui.Button[Any] = discord.ui.Button(
            label="Previous",
            style=discord.ButtonStyle.secondary,
            emoji="⬅️",
            disabled=(self.step <= 1),
            row=3,
        )

        async def _prev_cb(interaction: discord.Interaction) -> None:
            self.step = max(1, self.step - 1)
            self._rebuild_components()
            await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

        prev_btn.callback = _prev_cb
        self.add_item(prev_btn)

        next_btn: discord.ui.Button[Any] = discord.ui.Button(
            label="Next",
            style=discord.ButtonStyle.primary,
            emoji="➡️",
            disabled=(self.step >= self.TOTAL_STEPS),
            row=3,
        )

        async def _next_cb(interaction: discord.Interaction) -> None:
            self.step = min(self.TOTAL_STEPS, self.step + 1)
            self._rebuild_components()
            await interaction.response.edit_message(embed=self.build_step_embed(), view=self)

        next_btn.callback = _next_cb
        self.add_item(next_btn)

        save_btn: discord.ui.Button[Any] = discord.ui.Button(
            label="Save & Apply Configuration",
            style=discord.ButtonStyle.success,
            emoji="💾",
            row=3,
        )

        async def _save_cb(interaction: discord.Interaction) -> None:
            await self._persist_all_settings(interaction)

        save_btn.callback = _save_cb
        self.add_item(save_btn)

        cancel_btn: discord.ui.Button[Any] = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            emoji="✖️",
            row=3,
        )

        async def _cancel_cb(interaction: discord.Interaction) -> None:
            self.stop()
            await interaction.response.edit_message(
                embed=info_embed("Setup Wizard Cancelled", "No unsaved changes were applied."),
                view=None,
            )

        cancel_btn.callback = _cancel_cb
        self.add_item(cancel_btn)

    async def _persist_all_settings(self, interaction: discord.Interaction) -> None:
        d = self.draft
        gid = self.guild.id

        # 1. Core guild_configs + prefixes
        await self.bot.db.set_prefixes(gid, d.get("prefixes") or ["!"])
        await self.bot.db.update_guild_config(
            gid,
            admin_role_id=d.get("admin_role_id"),
            mod_role_id=d.get("mod_role_id"),
            support_role_id=d.get("support_role_id"),
            event_manager_role_id=d.get("event_manager_role_id"),
            modlog_channel_id=d.get("modlog_channel_id"),
            security_alert_channel_id=d.get("modlog_channel_id"),
            level_up_channel_id=d.get("level_up_channel_id"),
            protection_mode=d.get("protection_mode", "normal"),
            xp_enabled=int(bool(d.get("xp_enabled", True))),
        )

        # 2. Logging config
        if d.get("modlog_channel_id"):
            await self.bot.db.ensure_logging_config(gid)
            await self.bot.db.execute(
                "UPDATE logging_configs SET default_log_channel_id = ?, moderation_log_channel_id = ? WHERE guild_id = ?",
                (d["modlog_channel_id"], d["modlog_channel_id"], gid),
            )

        # 3. Welcome & Goodbye config
        await self.bot.db.ensure_welcome_config(gid)
        auto_roles = [d["welcome_auto_role_id"]] if d.get("welcome_auto_role_id") else []
        await self.bot.db.execute(
            """
            UPDATE welcome_configs
            SET welcome_enabled = ?, welcome_channel_id = ?, welcome_message = ?,
                welcome_auto_roles_json = ?, goodbye_enabled = ?, goodbye_channel_id = ?
            WHERE guild_id = ?
            """,
            (
                int(bool(d.get("welcome_channel_id"))),
                d.get("welcome_channel_id"),
                d.get("welcome_message"),
                json.dumps(auto_roles),
                int(bool(d.get("goodbye_channel_id"))),
                d.get("goodbye_channel_id"),
                gid,
            ),
        )

        # 4. Ticket config
        await self.bot.db.ensure_ticket_config(gid)
        sup_roles = [d["support_role_id"]] if d.get("support_role_id") else []
        await self.bot.db.execute(
            """
            UPDATE ticket_configs
            SET category_id = ?, log_channel_id = ?, transcript_channel_id = ?, support_roles_json = ?
            WHERE guild_id = ?
            """,
            (
                d.get("ticket_category_id"),
                d.get("modlog_channel_id"),
                d.get("modlog_channel_id"),
                json.dumps(sup_roles),
                gid,
            ),
        )

        # 5. Starter Autoresponder
        if d.get("starter_ar_trigger") and d.get("starter_ar_reply"):
            trigs = [t.strip() for t in str(d["starter_ar_trigger"]).split("|") if t.strip()]
            await self.bot.db.execute(
                """
                INSERT OR REPLACE INTO autoresponders (
                    guild_id, name, triggers_json, match_type, case_sensitive,
                    response_text, mention_user, reply_message, cooldown_seconds,
                    enabled, created_by, created_at
                ) VALUES (?, 'starter', ?, 'contains', 0, ?, 1, 1, 5, 1, ?, ?)
                """
                if not self.bot.db.is_postgres
                else """
                INSERT INTO autoresponders (
                    guild_id, name, triggers_json, match_type, case_sensitive,
                    response_text, mention_user, reply_message, cooldown_seconds,
                    enabled, created_by, created_at
                ) VALUES (?, 'starter', ?, 'contains', 0, ?, 1, 1, 5, 1, ?, ?)
                ON CONFLICT (guild_id, name) DO UPDATE SET triggers_json = EXCLUDED.triggers_json, response_text = EXCLUDED.response_text
                """,
                (
                    gid,
                    json.dumps(trigs),
                    d["starter_ar_reply"],
                    interaction.user.id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        # 6. Level 5 Role Reward
        if d.get("level_5_role_id"):
            await self.bot.db.execute(
                """
                INSERT OR REPLACE INTO role_rewards (guild_id, role_id, reward_type, threshold, remove_on_drop, stack_roles)
                VALUES (?, ?, 'level', 5, 1, 1)
                """
                if not self.bot.db.is_postgres
                else """
                INSERT INTO role_rewards (guild_id, role_id, reward_type, threshold, remove_on_drop, stack_roles)
                VALUES (?, ?, 'level', 5, 1, 1)
                ON CONFLICT (guild_id, role_id, reward_type) DO UPDATE SET threshold = 5
                """,
                (gid, d["level_5_role_id"]),
            )

        self.stop()
        await interaction.response.edit_message(
            embed=success_embed(
                "✨ Setup Wizard Complete — Configuration Saved!",
                f"All settings for **{self.guild.name}** have been saved to the database.\n"
                f"You can run `/setup` again anytime or fine-tune individual modules with `/permissions`, `/logging`, `/ticket`, `/welcome`, and `/antinuke`.",
            ),
            view=None,
        )


class SetupWizardCog(commands.Cog, name="SetupWizard"):
    """Interactive multi-step server setup wizard."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="setup",
        description="Launch the interactive multi-step Setup Wizard to configure Cosmic ki baddie.",
    )
    async def setup_cmd(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        g_cfg = await self.bot.db.ensure_guild(ctx.guild.id)
        w_cfg = await self.bot.db.ensure_welcome_config(ctx.guild.id)
        t_cfg = await self.bot.db.ensure_ticket_config(ctx.guild.id)
        prefixes = await self.bot.db.get_prefixes(ctx.guild.id)

        initial_draft: Dict[str, Any] = {
            "prefixes": prefixes,
            "modlog_channel_id": g_cfg.get("modlog_channel_id"),
            "welcome_channel_id": w_cfg.get("welcome_channel_id"),
            "welcome_enabled": bool(w_cfg.get("welcome_enabled")),
            "welcome_message": w_cfg.get("welcome_message"),
            "goodbye_channel_id": w_cfg.get("goodbye_channel_id"),
            "goodbye_enabled": bool(w_cfg.get("goodbye_enabled")),
            "ticket_category_id": t_cfg.get("category_id"),
            "level_up_channel_id": g_cfg.get("level_up_channel_id"),
            "admin_role_id": g_cfg.get("admin_role_id"),
            "mod_role_id": g_cfg.get("mod_role_id"),
            "support_role_id": g_cfg.get("support_role_id"),
            "event_manager_role_id": g_cfg.get("event_manager_role_id"),
            "protection_mode": g_cfg.get("protection_mode", "normal"),
            "xp_enabled": bool(g_cfg.get("xp_enabled", 1)),
            "antinuke_enabled": True,
        }
        view = SetupWizardView(self.bot, ctx.guild, ctx.author.id, initial_draft)
        await ctx.reply(embed=view.build_step_embed(), view=view)


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(SetupWizardCog(bot))
