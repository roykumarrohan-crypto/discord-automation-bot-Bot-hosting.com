"""
Cosmic ki baddie — Moderation & Punishment System Cog (`cogs/moderation.py`)
Implements all 25 core moderation commands + configurable warning escalation
and role punishment permissions:
/ban, /unban, /kick, /timeout, /untimeout, /warn, /warnings, /clearwarnings,
/purge, /slowmode, /lock, /unlock, /hide, /unhide, /nickname, /role, /mute,
/unmute, /snipe, /editsnipe, /userinfo, /serverinfo, /modlogs, /reason, /case,
plus /escalation and /modperm.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed
from utils.helpers import format_duration, parse_duration

if TYPE_CHECKING:
    from main import CosmicBot


class ModerationCog(commands.Cog, name="Moderation"):
    """Complete moderation suite with case IDs, hierarchy protection, and warning escalation."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    # =========================================================================
    # Internal Helpers
    # =========================================================================
    async def _notify_user(
        self,
        guild: discord.Guild,
        user: discord.abc.User,
        action_title: str,
        reason: str,
        duration_text: Optional[str] = None,
        case_number: Optional[int] = None,
    ) -> bool:
        """Send an automatic moderation notification DM to the target user."""
        try:
            embed = make_embed(
                title=f"🛡️ Moderation Notice — {guild.name}",
                description=f"**Action:** {action_title}\n**Reason:** {reason}",
                color=THEME.WARNING,
            )
            if duration_text:
                embed.add_field(name="Duration", value=duration_text, inline=True)
            if case_number is not None:
                embed.add_field(name="Case ID", value=f"`#{case_number}`", inline=True)
            await user.send(embed=embed)
            return True
        except Exception:
            return False

    async def _log_moderation_case(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.abc.User,
        moderator: discord.Member,
        reason: str,
        duration_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create a persistent moderation case and dispatch to LoggingCog."""
        case_row = await self.bot.db.create_mod_case(
            guild_id=guild.id,
            action=action,
            target_id=target.id,
            target_name=str(target),
            moderator_id=moderator.id,
            moderator_name=str(moderator),
            reason=reason,
            duration_seconds=duration_seconds,
        )
        logging_cog = self.bot.get_cog("Logging")
        if logging_cog and hasattr(logging_cog, "log_moderation_case"):
            msg_id = await logging_cog.log_moderation_case(guild, case_row)
            if msg_id:
                await self.bot.db.execute(
                    "UPDATE mod_cases SET log_message_id = ? WHERE id = ?",
                    (msg_id, case_row["id"]),
                )
        return case_row

    async def _ensure_mute_role(self, guild: discord.Guild) -> discord.Role:
        """Fetch or create a dedicated Muted role and apply channel overwrites."""
        cfg = await self.bot.db.ensure_guild(guild.id)
        mute_role_id = cfg.get("mute_role_id")
        role = guild.get_role(mute_role_id) if mute_role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name="Cosmic Muted")
        if role is None:
            role = await guild.create_role(
                name="Cosmic Muted",
                color=discord.Color.dark_grey(),
                reason="Cosmic ki baddie automatic Mute Role initialization",
            )
            for channel in guild.channels:
                try:
                    await channel.set_permissions(
                        role,
                        send_messages=False,
                        add_reactions=False,
                        speak=False,
                        send_messages_in_threads=False,
                    )
                except Exception:
                    continue
        await self.bot.db.update_guild_config(guild.id, mute_role_id=role.id)
        return role

    async def _evaluate_warning_escalation(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        active_warnings: int,
    ) -> Optional[str]:
        """Check `warning_escalations` table and execute automatic punishment if threshold met."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        esc = await self.bot.db.fetchone(
            "SELECT * FROM warning_escalations WHERE guild_id = ? AND warn_count = ?",
            (ctx.guild.id, active_warnings),
        )
        if not esc:
            return None

        action = esc["action"].lower()
        duration_sec = int(esc.get("duration_seconds") or 3600)
        reason = str(esc.get("reason_template") or "Automatic escalation at {count} warnings").replace(
            "{count}", str(active_warnings)
        )

        try:
            if action == "timeout":
                until = datetime.now(timezone.utc) + timedelta(seconds=min(duration_sec, 2419200))
                await member.timeout(until, reason=reason)
                case = await self._log_moderation_case(
                    ctx.guild, "timeout (auto-escalation)", member, ctx.author, reason, duration_sec
                )
                return f"⚡ **Auto-Escalation Triggered ({active_warnings} Warnings):** Timed out for **{format_duration(duration_sec)}** (Case `#{case['case_number']}`)."
            if action == "mute":
                mute_role = await self._ensure_mute_role(ctx.guild)
                await member.add_roles(mute_role, reason=reason)
                case = await self._log_moderation_case(
                    ctx.guild, "mute (auto-escalation)", member, ctx.author, reason, duration_sec
                )
                return f"⚡ **Auto-Escalation Triggered ({active_warnings} Warnings):** Muted (Case `#{case['case_number']}`)."
            if action == "kick":
                await member.kick(reason=reason)
                case = await self._log_moderation_case(
                    ctx.guild, "kick (auto-escalation)", member, ctx.author, reason
                )
                return f"⚡ **Auto-Escalation Triggered ({active_warnings} Warnings):** Kicked from server (Case `#{case['case_number']}`)."
            if action == "ban":
                await ctx.guild.ban(member, reason=reason, delete_message_days=0)
                case = await self._log_moderation_case(
                    ctx.guild, "ban (auto-escalation)", member, ctx.author, reason
                )
                return f"⚡ **Auto-Escalation Triggered ({active_warnings} Warnings):** Banned from server (Case `#{case['case_number']}`)."
        except Exception as exc:
            return f"⚠️ Auto-escalation (`{action}`) could not be applied: `{exc}`"
        return None

    # =========================================================================
    # 1. /ban & /unban
    # =========================================================================
    @commands.hybrid_command(name="ban", description="Ban a member or user ID from the server.")
    @app_commands.describe(
        user="Member or User to ban",
        reason="Reason for the ban",
        delete_days="Number of days of messages to delete (0-7)",
    )
    async def ban_cmd(
        self,
        ctx: commands.Context[Any],
        user: discord.User,
        delete_days: Optional[int] = 0,
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "ban"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        target_member = ctx.guild.get_member(user.id)
        if target_member is not None:
            ok, msg = self.bot.permissions_engine.check_hierarchy(
                ctx.guild, ctx.author, target_member, ctx.guild.me
            )
            if not ok:
                await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
                return

        del_days = max(0, min(7, delete_days or 0))
        await self._notify_user(ctx.guild, user, "Permanent Ban", reason)
        await ctx.guild.ban(
            user,
            reason=f"[{ctx.author} ({ctx.author.id})] {reason}",
            delete_message_days=del_days,
        )
        case = await self._log_moderation_case(ctx.guild, "ban", user, ctx.author, reason)
        await ctx.reply(
            embed=success_embed(
                f"Member Banned — Case #{case['case_number']}",
                f"**Target:** {user} (`{user.id}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}",
            )
        )

    @commands.hybrid_command(name="unban", description="Unban a previously banned user by ID or tag.")
    @app_commands.describe(user_id="The Discord User ID to unban", reason="Reason for unbanning")
    async def unban_cmd(
        self,
        ctx: commands.Context[Any],
        user_id: str,
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "unban"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        cleaned_id = user_id.strip().strip("<@!>")
        if not cleaned_id.isdigit():
            await ctx.reply(
                embed=error_embed("Invalid User ID", "Please provide a valid numeric Discord User ID."),
                ephemeral=True,
            )
            return

        uid = int(cleaned_id)
        target_obj = discord.Object(id=uid)
        try:
            ban_entry = await ctx.guild.fetch_ban(target_obj)
            target_user = ban_entry.user
        except discord.NotFound:
            await ctx.reply(
                embed=error_embed("Not Banned", f"User ID `{uid}` is not currently banned."),
                ephemeral=True,
            )
            return

        await ctx.guild.unban(target_user, reason=f"[{ctx.author}] {reason}")
        case = await self._log_moderation_case(ctx.guild, "unban", target_user, ctx.author, reason)
        await ctx.reply(
            embed=success_embed(
                f"User Unbanned — Case #{case['case_number']}",
                f"**User:** {target_user} (`{target_user.id}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}",
            )
        )

    # =========================================================================
    # 2. /kick
    # =========================================================================
    @commands.hybrid_command(name="kick", description="Kick a member from the server.")
    @app_commands.describe(member="Member to kick", reason="Reason for kicking")
    async def kick_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "kick"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        ok, msg = self.bot.permissions_engine.check_hierarchy(
            ctx.guild, ctx.author, member, ctx.guild.me
        )
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        await self._notify_user(ctx.guild, member, "Server Kick", reason)
        await member.kick(reason=f"[{ctx.author}] {reason}")
        case = await self._log_moderation_case(ctx.guild, "kick", member, ctx.author, reason)
        await ctx.reply(
            embed=success_embed(
                f"Member Kicked — Case #{case['case_number']}",
                f"**Target:** {member} (`{member.id}`)\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}",
            )
        )

    # =========================================================================
    # 3. /timeout & /untimeout
    # =========================================================================
    @commands.hybrid_command(
        name="timeout",
        description="Timeout (communication disable) a member for a specified duration.",
    )
    @app_commands.describe(
        member="Member to timeout",
        duration="Duration (e.g. 10m, 2h, 1d, 7d — max 28d)",
        reason="Reason for timeout",
    )
    async def timeout_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        duration: str = "10m",
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "timeout"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        ok, msg = self.bot.permissions_engine.check_hierarchy(
            ctx.guild, ctx.author, member, ctx.guild.me
        )
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        seconds = parse_duration(duration)
        if not seconds or seconds < 10 or seconds > 2419200:
            await ctx.reply(
                embed=error_embed(
                    "Invalid Duration",
                    "Please provide a valid timeout duration between `10s` and `28d` (e.g. `15m`, `2h`, `3d`).",
                ),
                ephemeral=True,
            )
            return

        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await member.timeout(until, reason=f"[{ctx.author}] {reason}")
        dur_str = format_duration(seconds)
        case = await self._log_moderation_case(
            ctx.guild, "timeout", member, ctx.author, reason, seconds
        )
        await self._notify_user(
            ctx.guild, member, "Timeout", reason, dur_str, case["case_number"]
        )
        await ctx.reply(
            embed=success_embed(
                f"Member Timed Out — Case #{case['case_number']}",
                f"**Target:** {member.mention} (`{member.id}`)\n"
                f"**Duration:** {dur_str} (until {discord.utils.format_dt(until, 'f')})\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}",
            )
        )

    @commands.hybrid_command(
        name="untimeout", description="Remove an active timeout from a member."
    )
    @app_commands.describe(member="Member to remove timeout from", reason="Reason")
    async def untimeout_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        reason: str = "Timeout lifted by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        ok, msg = self.bot.permissions_engine.check_hierarchy(
            ctx.guild, ctx.author, member, ctx.guild.me
        )
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        await member.timeout(None, reason=f"[{ctx.author}] {reason}")
        case = await self._log_moderation_case(ctx.guild, "untimeout", member, ctx.author, reason)
        await ctx.reply(
            embed=success_embed(
                f"Timeout Removed — Case #{case['case_number']}",
                f"**Target:** {member.mention}\n"
                f"**Moderator:** {ctx.author.mention}\n"
                f"**Reason:** {reason}",
            )
        )

    # =========================================================================
    # 4. /warn, /warnings, /clearwarnings & /escalation
    # =========================================================================
    @commands.hybrid_command(
        name="warn",
        description="Issue an official warning to a member and evaluate auto-escalation.",
    )
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    async def warn_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        reason: str,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "warn"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        ok, msg = self.bot.permissions_engine.check_hierarchy(ctx.guild, ctx.author, member)
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        case = await self._log_moderation_case(ctx.guild, "warn", member, ctx.author, reason)
        active_count = await self.bot.db.add_warning(
            ctx.guild.id, member.id, ctx.author.id, case["case_number"], reason
        )
        await self._notify_user(
            ctx.guild,
            member,
            f"Official Warning (#{active_count})",
            reason,
            case_number=case["case_number"],
        )

        escalation_note = await self._evaluate_warning_escalation(ctx, member, active_count)
        desc = (
            f"**Target:** {member.mention} (`{member.id}`)\n"
            f"**Active Warnings:** `{active_count}`\n"
            f"**Moderator:** {ctx.author.mention}\n"
            f"**Reason:** {reason}"
        )
        if escalation_note:
            desc += f"\n\n{escalation_note}"

        await ctx.reply(
            embed=warning_embed(f"Member Warned — Case #{case['case_number']}", desc)
        )

    @commands.hybrid_command(
        name="warnings", description="View all active warnings for a member."
    )
    @app_commands.describe(member="Member whose warnings to view")
    async def warnings_cmd(
        self, ctx: commands.Context[Any], member: discord.Member
    ) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.get_warnings(ctx.guild.id, member.id, active_only=True)
        if not rows:
            await ctx.reply(
                embed=info_embed(
                    "Member Warnings",
                    f"✨ {member.mention} has **0** active warnings.",
                )
            )
            return

        lines = [
            f"• **Case #{r['case_number']}** — {r['reason']} *(by <@{r['moderator_id']}> on `{r['created_at'][:10]}`)*"
            for r in rows[:15]
        ]
        embed = make_embed(
            title=f"⚠️ Active Warnings — {member} ({len(rows)})",
            description="\n".join(lines),
            color=THEME.WARNING,
            thumbnail_url=member.display_avatar.url if member.display_avatar else None,
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="clearwarnings", description="Clear all active warnings for a member."
    )
    @app_commands.describe(member="Member whose warnings to clear", reason="Reason for clearing")
    async def clearwarnings_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        reason: str = "Warnings pardoned by staff",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        cleared = await self.bot.db.clear_warnings(ctx.guild.id, member.id)
        case = await self._log_moderation_case(
            ctx.guild, "clearwarnings", member, ctx.author, f"{reason} ({cleared} cleared)"
        )
        await ctx.reply(
            embed=success_embed(
                f"Warnings Cleared — Case #{case['case_number']}",
                f"Cleared **{cleared}** active warning(s) for {member.mention}.\n**Reason:** {reason}",
            )
        )

    @commands.hybrid_group(
        name="escalation",
        description="Configure automatic warning escalation rules.",
        invoke_without_command=True,
    )
    async def escalation_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM warning_escalations WHERE guild_id = ? ORDER BY warn_count ASC",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed(
                    "Warning Escalation Rules",
                    "No automatic warning escalation thresholds configured yet.\n"
                    "Use `/escalation set <warnings> <timeout|mute|kick|ban> [duration]` to add one.",
                )
            )
            return
        lines = [
            f"• **{r['warn_count']} Warnings** → `{r['action'].upper()}` "
            f"({format_duration(r['duration_seconds']) if r['action'] in ('timeout', 'mute') else 'Immediate'})"
            for r in rows
        ]
        await ctx.reply(embed=info_embed("Warning Escalation Rules", "\n".join(lines)))

    @escalation_group.command(
        name="set",
        description="Set an automatic punishment when a member reaches N warnings.",
    )
    @app_commands.describe(
        warn_count="Number of active warnings that triggers this action (e.g. 3)",
        action="Punishment action: timeout, mute, kick, or ban",
        duration="Duration for timeout/mute (e.g. 1h, 1d)",
    )
    async def escalation_set(
        self,
        ctx: commands.Context[Any],
        warn_count: int,
        action: str,
        duration: str = "1h",
    ) -> None:
        if ctx.guild is None:
            return
        act = action.lower().strip()
        if act not in ("timeout", "mute", "kick", "ban"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Action", "Action must be one of: `timeout`, `mute`, `kick`, `ban`."
                ),
                ephemeral=True,
            )
            return
        dur_sec = parse_duration(duration) or 3600
        existing = await self.bot.db.fetchone(
            "SELECT warn_count FROM warning_escalations WHERE guild_id = ? AND warn_count = ?",
            (ctx.guild.id, warn_count),
        )
        if existing:
            await self.bot.db.execute(
                "UPDATE warning_escalations SET action = ?, duration_seconds = ? WHERE guild_id = ? AND warn_count = ?",
                (act, dur_sec, ctx.guild.id, warn_count),
            )
        else:
            await self.bot.db.execute(
                "INSERT INTO warning_escalations (guild_id, warn_count, action, duration_seconds) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, warn_count, act, dur_sec),
            )
        await ctx.reply(
            embed=success_embed(
                "Escalation Rule Saved",
                f"At **{warn_count}** warnings, members will receive: **{act.upper()}** ({format_duration(dur_sec)}).",
            )
        )

    @escalation_group.command(
        name="remove", description="Remove a warning escalation rule."
    )
    async def escalation_remove(self, ctx: commands.Context[Any], warn_count: int) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.execute(
            "DELETE FROM warning_escalations WHERE guild_id = ? AND warn_count = ?",
            (ctx.guild.id, warn_count),
        )
        await ctx.reply(
            embed=success_embed(
                "Escalation Rule Removed",
                f"Removed escalation rule for **{warn_count}** warnings.",
            )
        )

    @commands.hybrid_command(
        name="modperm",
        description="Configure which punishment actions a specific moderator role can execute.",
    )
    @app_commands.describe(
        role="Moderator role to configure",
        actions="Comma-separated allowed actions (warn,timeout,mute,kick,ban,unban,purge or *)",
    )
    async def modperm_cmd(
        self, ctx: commands.Context[Any], role: discord.Role, *, actions: str
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not self.bot.permissions_engine.is_emergency_admin(ctx.author):
            await ctx.reply(
                embed=error_embed("Admin Required", "Only Server Administrators can configure moderator punishment tiers."),
                ephemeral=True,
            )
            return
        act_list = [a.strip().lower() for a in actions.split(",") if a.strip()]
        existing = await self.bot.db.fetchone(
            "SELECT role_id FROM mod_role_permissions WHERE guild_id = ? AND role_id = ?",
            (ctx.guild.id, role.id),
        )
        if existing:
            await self.bot.db.execute(
                "UPDATE mod_role_permissions SET allowed_actions_json = ? WHERE guild_id = ? AND role_id = ?",
                (json.dumps(act_list), ctx.guild.id, role.id),
            )
        else:
            await self.bot.db.execute(
                "INSERT INTO mod_role_permissions (guild_id, role_id, allowed_actions_json) VALUES (?, ?, ?)",
                (ctx.guild.id, role.id, json.dumps(act_list)),
            )
        await ctx.reply(
            embed=success_embed(
                "Moderator Role Permissions Updated",
                f"Role {role.mention} is now authorized for punishment actions: `{', '.join(act_list)}`",
            )
        )

    # =========================================================================
    # 5. /purge, /slowmode, /lock, /unlock, /hide, /unhide
    # =========================================================================
    @commands.hybrid_command(
        name="purge", description="Bulk-delete a specified number of messages in the current channel."
    )
    @app_commands.describe(
        amount="Number of messages to delete (1-200)",
        target="Optional: only delete messages from this user",
    )
    async def purge_cmd(
        self,
        ctx: commands.Context[Any],
        amount: int,
        target: Optional[discord.Member] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "purge"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        count = max(1, min(200, amount))
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.interaction.response.defer(ephemeral=True)

        check_fn = (lambda m: m.author.id == target.id) if target else (lambda _: True)
        deleted = await ctx.channel.purge(limit=count, check=check_fn)
        case = await self._log_moderation_case(
            ctx.guild,
            "purge",
            target or ctx.author,
            ctx.author,
            f"Purged {len(deleted)} messages in #{ctx.channel.name}",
        )
        embed = success_embed(
            f"Messages Purged — Case #{case['case_number']}",
            f"Deleted **{len(deleted)}** message(s) in {ctx.channel.mention}"
            + (f" from {target.mention}." if target else "."),
        )
        if ctx.interaction:
            await ctx.interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.channel.send(embed=embed, delete_after=6)

    @commands.hybrid_command(
        name="slowmode", description="Set the slowmode delay in seconds for a text channel."
    )
    @app_commands.describe(
        seconds="Slowmode delay in seconds (0 to disable, max 21600)",
        channel="Channel to modify (defaults to current channel)",
    )
    async def slowmode_cmd(
        self,
        ctx: commands.Context[Any],
        seconds: int,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        delay = max(0, min(21600, seconds))
        await target_ch.edit(slowmode_delay=delay, reason=f"Slowmode set by {ctx.author}")
        await self._log_moderation_case(
            ctx.guild,
            "slowmode",
            ctx.author,
            ctx.author,
            f"Set slowmode in #{target_ch.name} to {delay}s",
        )
        await ctx.reply(
            embed=success_embed(
                "Slowmode Updated",
                f"Slowmode for {target_ch.mention} is now **{format_duration(delay) if delay else 'Disabled (0s)'}**.",
            )
        )

    @commands.hybrid_command(
        name="lock", description="Lock a channel so regular members cannot send messages."
    )
    @app_commands.describe(
        channel="Channel to lock (defaults to current channel)",
        reason="Reason for locking the channel",
    )
    async def lock_cmd(
        self,
        ctx: commands.Context[Any],
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: str = "Channel locked by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        overwrite = target_ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await target_ch.set_permissions(
            ctx.guild.default_role, overwrite=overwrite, reason=f"[{ctx.author}] {reason}"
        )
        case = await self._log_moderation_case(
            ctx.guild, "lock", ctx.author, ctx.author, f"Locked #{target_ch.name}: {reason}"
        )
        await ctx.reply(
            embed=warning_embed(
                f"🔒 Channel Locked — Case #{case['case_number']}",
                f"{target_ch.mention} has been locked.\n**Reason:** {reason}",
            )
        )

    @commands.hybrid_command(
        name="unlock", description="Unlock a previously locked channel for regular members."
    )
    @app_commands.describe(
        channel="Channel to unlock (defaults to current channel)",
        reason="Reason for unlocking",
    )
    async def unlock_cmd(
        self,
        ctx: commands.Context[Any],
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: str = "Channel unlocked by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        overwrite = target_ch.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await target_ch.set_permissions(
            ctx.guild.default_role, overwrite=overwrite, reason=f"[{ctx.author}] {reason}"
        )
        case = await self._log_moderation_case(
            ctx.guild, "unlock", ctx.author, ctx.author, f"Unlocked #{target_ch.name}: {reason}"
        )
        await ctx.reply(
            embed=success_embed(
                f"🔓 Channel Unlocked — Case #{case['case_number']}",
                f"{target_ch.mention} is now unlocked.\n**Reason:** {reason}",
            )
        )

    @commands.hybrid_command(
        name="hide", description="Hide a channel from @everyone view permissions."
    )
    @app_commands.describe(channel="Channel to hide", reason="Reason for hiding")
    async def hide_cmd(
        self,
        ctx: commands.Context[Any],
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: str = "Channel hidden by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        overwrite = target_ch.overwrites_for(ctx.guild.default_role)
        overwrite.view_channel = False
        await target_ch.set_permissions(
            ctx.guild.default_role, overwrite=overwrite, reason=f"[{ctx.author}] {reason}"
        )
        await self._log_moderation_case(
            ctx.guild, "hide", ctx.author, ctx.author, f"Hid #{target_ch.name}: {reason}"
        )
        await ctx.reply(
            embed=success_embed(
                "Channel Hidden",
                f"👁️‍🗨️ {target_ch.mention} is now hidden from `@everyone`.",
            )
        )

    @commands.hybrid_command(
        name="unhide", description="Make a hidden channel visible to @everyone again."
    )
    @app_commands.describe(channel="Channel to unhide", reason="Reason for unhiding")
    async def unhide_cmd(
        self,
        ctx: commands.Context[Any],
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: str = "Channel unhidden by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        overwrite = target_ch.overwrites_for(ctx.guild.default_role)
        overwrite.view_channel = None
        await target_ch.set_permissions(
            ctx.guild.default_role, overwrite=overwrite, reason=f"[{ctx.author}] {reason}"
        )
        await self._log_moderation_case(
            ctx.guild, "unhide", ctx.author, ctx.author, f"Unhid #{target_ch.name}: {reason}"
        )
        await ctx.reply(
            embed=success_embed(
                "Channel Unhidden",
                f"👁️ {target_ch.mention} is now visible to `@everyone`.",
            )
        )

    # =========================================================================
    # 6. /nickname, /role, /mute, /unmute
    # =========================================================================
    @commands.hybrid_command(
        name="nickname", description="Change or reset a member's server nickname."
    )
    @app_commands.describe(
        member="Member whose nickname to change",
        new_nickname="New nickname (leave blank or 'reset' to clear)",
    )
    async def nickname_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        new_nickname: Optional[str] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        ok, msg = self.bot.permissions_engine.check_hierarchy(
            ctx.guild, ctx.author, member, ctx.guild.me
        )
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        nick = None if not new_nickname or new_nickname.lower() == "reset" else new_nickname[:32]
        old_nick = member.display_name
        await member.edit(nick=nick, reason=f"Nickname updated by {ctx.author}")
        await self._log_moderation_case(
            ctx.guild,
            "nickname",
            member,
            ctx.author,
            f"Changed nickname from '{old_nick}' to '{nick or member.name}'",
        )
        await ctx.reply(
            embed=success_embed(
                "Nickname Updated",
                f"**Member:** {member.mention}\n"
                f"**Before:** `{old_nick}`\n"
                f"**After:** `{nick or member.name}`",
            )
        )

    @commands.hybrid_command(
        name="role",
        description="Toggle (assign or remove) a role on a member with hierarchy validation.",
    )
    @app_commands.describe(member="Target member", role="Role to assign or remove")
    async def role_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        role: discord.Role,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            await ctx.reply(
                embed=error_embed(
                    "Hierarchy Protection",
                    f"You cannot manage {role.mention} because it is equal to or higher than your top role.",
                ),
                ephemeral=True,
            )
            return
        if ctx.guild.me and role >= ctx.guild.me.top_role:
            await ctx.reply(
                embed=error_embed(
                    "Bot Hierarchy Restriction",
                    f"My highest role is below {role.mention}, so Discord prevents me from assigning it.",
                ),
                ephemeral=True,
            )
            return

        if role in member.roles:
            await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
            action_label = "Removed role"
        else:
            await member.add_roles(role, reason=f"Role assigned by {ctx.author}")
            action_label = "Assigned role"

        await self._log_moderation_case(
            ctx.guild,
            "role",
            member,
            ctx.author,
            f"{action_label} @{role.name} ({role.id})",
        )
        await ctx.reply(
            embed=success_embed(
                "Role Updated",
                f"**{action_label}** {role.mention} on {member.mention}.",
            )
        )

    @commands.hybrid_command(
        name="mute",
        description="Mute a member across text and voice channels using the server Mute role.",
    )
    @app_commands.describe(
        member="Member to mute",
        duration="Optional mute duration (e.g. 30m, 2h, 1d)",
        reason="Reason for muting",
    )
    async def mute_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        duration: Optional[str] = None,
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        allowed_act, act_msg = await self.bot.permissions_engine.check_mod_action_permission(
            ctx.guild, ctx.author, "mute"
        )
        if not allowed_act:
            await ctx.reply(embed=error_embed("Punishment Restricted", act_msg), ephemeral=True)
            return

        ok, msg = self.bot.permissions_engine.check_hierarchy(
            ctx.guild, ctx.author, member, ctx.guild.me
        )
        if not ok:
            await ctx.reply(embed=error_embed("Hierarchy Protection", msg), ephemeral=True)
            return

        mute_role = await self._ensure_mute_role(ctx.guild)
        await member.add_roles(mute_role, reason=f"[{ctx.author}] {reason}")

        dur_sec = parse_duration(duration) if duration else None
        if dur_sec:
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=dur_sec)).isoformat()
            await self.bot.db.execute(
                """
                INSERT OR REPLACE INTO temp_roles (guild_id, user_id, role_id, assigned_by, expires_at, active)
                VALUES (?, ?, ?, ?, ?, 1)
                """
                if not self.bot.db.is_postgres
                else """
                INSERT INTO temp_roles (guild_id, user_id, role_id, assigned_by, expires_at, active)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT (guild_id, user_id, role_id) DO UPDATE SET expires_at = EXCLUDED.expires_at, active = 1
                """,
                (ctx.guild.id, member.id, mute_role.id, ctx.author.id, expires_at),
            )

        case = await self._log_moderation_case(
            ctx.guild, "mute", member, ctx.author, reason, dur_sec
        )
        await self._notify_user(
            ctx.guild,
            member,
            "Server Mute",
            reason,
            format_duration(dur_sec),
            case["case_number"],
        )
        await ctx.reply(
            embed=success_embed(
                f"Member Muted — Case #{case['case_number']}",
                f"**Target:** {member.mention}\n"
                f"**Role:** {mute_role.mention}\n"
                f"**Duration:** {format_duration(dur_sec)}\n"
                f"**Reason:** {reason}",
            )
        )

    @commands.hybrid_command(
        name="unmute", description="Unmute a member by removing the server Mute role."
    )
    @app_commands.describe(member="Member to unmute", reason="Reason for unmuting")
    async def unmute_cmd(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        *,
        reason: str = "Unmuted by moderator",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        mute_role = await self._ensure_mute_role(ctx.guild)
        if mute_role in member.roles:
            await member.remove_roles(mute_role, reason=f"[{ctx.author}] {reason}")
        await self.bot.db.execute(
            "UPDATE temp_roles SET active = 0 WHERE guild_id = ? AND user_id = ? AND role_id = ?",
            (ctx.guild.id, member.id, mute_role.id),
        )
        case = await self._log_moderation_case(ctx.guild, "unmute", member, ctx.author, reason)
        await ctx.reply(
            embed=success_embed(
                f"Member Unmuted — Case #{case['case_number']}",
                f"**Target:** {member.mention}\n**Reason:** {reason}",
            )
        )

    # =========================================================================
    # 7. /snipe, /editsnipe, /userinfo, /serverinfo, /modlogs, /reason, /case
    # =========================================================================
    @commands.hybrid_command(
        name="snipe",
        description="Retrieve the most recently deleted message in the current channel.",
    )
    async def snipe_cmd(self, ctx: commands.Context[Any]) -> None:
        data = self.bot.snipe_cache.get(ctx.channel.id)
        if not data:
            await ctx.reply(
                embed=info_embed("Nothing to Snipe", "No recently deleted messages recorded in this channel."),
                ephemeral=True,
            )
            return
        embed = make_embed(
            title="🎯 Sniped Deleted Message",
            description=data.get("content") or "*(Attachment / Embed only)*",
            color=THEME.SECONDARY,
            author_name=f"{data.get('author_name')} ({data.get('author_id')})",
            author_icon=data.get("author_avatar"),
        )
        if data.get("attachments"):
            embed.add_field(name="Attachments", value="\n".join(data["attachments"][:5]), inline=False)
        embed.add_field(name="Deleted", value=data.get("deleted_at", "Just now"), inline=True)
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="editsnipe",
        description="Retrieve the most recently edited message's original text in this channel.",
    )
    async def editsnipe_cmd(self, ctx: commands.Context[Any]) -> None:
        data = self.bot.editsnipe_cache.get(ctx.channel.id)
        if not data:
            await ctx.reply(
                embed=info_embed("Nothing to Edit-Snipe", "No recently edited messages recorded in this channel."),
                ephemeral=True,
            )
            return
        embed = make_embed(
            title="✏️ Edit-Sniped Message",
            color=THEME.ACCENT,
            author_name=f"{data.get('author_name')} ({data.get('author_id')})",
            author_icon=data.get("author_avatar"),
        )
        embed.add_field(name="Before", value=(data.get("before") or "*Empty*")[:1024], inline=False)
        embed.add_field(name="After", value=(data.get("after") or "*Empty*")[:1024], inline=False)
        if data.get("jump_url"):
            embed.add_field(name="Jump", value=f"[View Message]({data['jump_url']})", inline=True)
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="userinfo",
        description="Display detailed profile, role, activity, and moderation info for a member.",
    )
    @app_commands.describe(member="Member to inspect (defaults to yourself)")
    async def userinfo_cmd(
        self, ctx: commands.Context[Any], member: Optional[discord.Member] = None
    ) -> None:
        if ctx.guild is None:
            return
        target = member or (ctx.author if isinstance(ctx.author, discord.Member) else None)
        if target is None:
            return

        warnings = await self.bot.db.get_warnings(ctx.guild.id, target.id, active_only=True)
        act_row = await self.bot.db.fetchone(
            "SELECT * FROM member_activity WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, target.id),
        )
        roles = [r.mention for r in reversed(target.roles) if r.id != ctx.guild.default_role.id]
        roles_display = ", ".join(roles[:15]) + (f" (+{len(roles) - 15} more)" if len(roles) > 15 else "")

        embed = make_embed(
            title=f"👤 Member Profile — {target}",
            color=target.color.value if target.color.value else THEME.PRIMARY,
            thumbnail_url=target.display_avatar.url if target.display_avatar else None,
        )
        embed.add_field(name="User ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Nickname", value=f"`{target.nick or 'None'}`", inline=True)
        embed.add_field(name="Top Role", value=target.top_role.mention, inline=True)
        embed.add_field(
            name="Account Created",
            value=discord.utils.format_dt(target.created_at, "R") if target.created_at else "Unknown",
            inline=True,
        )
        embed.add_field(
            name="Joined Server",
            value=discord.utils.format_dt(target.joined_at, "R") if target.joined_at else "Unknown",
            inline=True,
        )
        embed.add_field(name="Active Warnings", value=f"`{len(warnings)}`", inline=True)
        if act_row:
            embed.add_field(
                name="Activity & Level",
                value=f"Level **{act_row['level']}** (`{act_row['xp']} XP` • `{act_row['messages_count']}` msgs • `{act_row['voice_minutes']}m` voice)",
                inline=False,
            )
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=roles_display or "None",
            inline=False,
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="serverinfo",
        description="Display comprehensive statistics and security posture for Side Quest Studio.",
    )
    async def serverinfo_cmd(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        g = ctx.guild
        cfg = await self.bot.db.ensure_guild(g.id)
        prefixes = await self.bot.db.get_prefixes(g.id)

        embed = make_embed(
            title=f"🌌 {g.name} — Server Overview",
            description=g.description or f"Managed by **{CONFIG.bot_name}**",
            color=THEME.PRIMARY,
            thumbnail_url=g.icon.url if g.icon else None,
        )
        embed.add_field(name="Server ID", value=f"`{g.id}`", inline=True)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
        embed.add_field(
            name="Created",
            value=discord.utils.format_dt(g.created_at, "R") if g.created_at else "Unknown",
            inline=True,
        )
        embed.add_field(name="Members", value=f"`{g.member_count or len(g.members)}`", inline=True)
        embed.add_field(
            name="Channels",
            value=f"`{len(g.text_channels)}` Text • `{len(g.voice_channels)}` Voice • `{len(g.categories)}` Categories",
            inline=True,
        )
        embed.add_field(name="Roles & Emojis", value=f"`{len(g.roles)}` Roles • `{len(g.emojis)}` Emojis", inline=True)
        embed.add_field(
            name="Protection Posture",
            value=f"Mode: **{str(cfg.get('protection_mode', 'normal')).upper()}** • Safe Mode: **{'ON 🛡️' if cfg.get('safe_mode') else 'OFF'}**",
            inline=False,
        )
        embed.add_field(
            name="Bot Prefixes",
            value=", ".join(f"`{p}`" for p in prefixes),
            inline=True,
        )
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="modlogs",
        description="View moderation case history for a specific user or the entire server.",
    )
    @app_commands.describe(user="Optional user to filter moderation logs by")
    async def modlogs_cmd(
        self, ctx: commands.Context[Any], user: Optional[discord.User] = None
    ) -> None:
        if ctx.guild is None:
            return
        cases = await self.bot.db.get_user_modlogs(
            ctx.guild.id, target_id=user.id if user else None, limit=15
        )
        if not cases:
            await ctx.reply(
                embed=info_embed(
                    "Moderation Logs",
                    f"No moderation cases found{f' for {user.mention}' if user else ''}.",
                )
            )
            return

        lines = [
            f"• **Case #{c['case_number']}** [`{c['action'].upper()}`] — <@{c['target_id']}> "
            f"by <@{c['moderator_id']}>: *{c['reason'][:80]}* (`{c['created_at'][:10]}`)"
            for c in cases
        ]
        await ctx.reply(
            embed=make_embed(
                title=f"📋 Moderation Logs {f'— {user}' if user else f'— {ctx.guild.name}'}",
                description="\n".join(lines),
                color=THEME.INFO,
            )
        )

    @commands.hybrid_command(
        name="case", description="Inspect full details of a specific moderation Case ID."
    )
    @app_commands.describe(case_number="The numeric Case ID (e.g. 1)")
    async def case_cmd(self, ctx: commands.Context[Any], case_number: int) -> None:
        if ctx.guild is None:
            return
        c = await self.bot.db.get_mod_case(ctx.guild.id, case_number)
        if not c:
            await ctx.reply(
                embed=error_embed("Case Not Found", f"Moderation Case `#{case_number}` does not exist."),
                ephemeral=True,
            )
            return
        embed = make_embed(
            title=f"📁 Moderation Case #{c['case_number']} — {c['action'].upper()}",
            color=THEME.INFO,
        )
        embed.add_field(name="Target", value=f"<@{c['target_id']}> (`{c['target_id']}`)", inline=True)
        embed.add_field(name="Moderator", value=f"<@{c['moderator_id']}> (`{c['moderator_id']}`)", inline=True)
        embed.add_field(name="Date", value=f"`{c['created_at'][:19].replace('T', ' ')} UTC`", inline=True)
        if c.get("duration_seconds"):
            embed.add_field(
                name="Duration", value=format_duration(c["duration_seconds"]), inline=True
            )
        embed.add_field(name="Reason", value=c["reason"], inline=False)
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="reason", description="Update the recorded reason for a moderation Case ID."
    )
    @app_commands.describe(case_number="The numeric Case ID", new_reason="Updated reason text")
    async def reason_cmd(
        self, ctx: commands.Context[Any], case_number: int, *, new_reason: str
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        updated = await self.bot.db.update_mod_case_reason(
            ctx.guild.id, case_number, new_reason
        )
        if not updated:
            await ctx.reply(
                embed=error_embed("Case Not Found", f"Moderation Case `#{case_number}` was not found."),
                ephemeral=True,
            )
            return
        await ctx.reply(
            embed=success_embed(
                f"Case #{case_number} Reason Updated",
                f"**New Reason:** {new_reason}",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(ModerationCog(bot))
