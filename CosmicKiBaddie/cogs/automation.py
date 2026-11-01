"""
Cosmic ki baddie — Advanced Automation & Activity Progression Engine (`cogs/automation.py`)
Implements:
- Message & Voice activity XP tracking, configurable cooldowns, anti-spam protection
- Activity milestones & Level-based role rewards (with automatic role removal when requirements drop or non-stacking tiers upgrade)
- Scheduled one-time messages & repeating announcements
- Event-driven automation rules (join/leave/reaction/milestone/channel creation)
"""

from __future__ import annotations

import json
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG, THEME
from utils.embeds import embed_from_dict, error_embed, info_embed, make_embed, success_embed
from utils.helpers import (
    build_progress_bar,
    format_duration,
    level_from_xp,
    parse_duration,
    render_template,
    xp_for_level,
)

if TYPE_CHECKING:
    from main import CosmicBot


class AutomationCog(commands.Cog, name="Automation"):
    """Complete server automation engine, scheduled announcements, and activity-based role progression."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot
        self._xp_cooldowns: Dict[Tuple[int, int], float] = {}
        self._spam_buckets: Dict[Tuple[int, int], Deque[float]] = defaultdict(deque)

    async def cog_load(self) -> None:
        if not self.scheduled_tasks_loop.is_running():
            self.scheduled_tasks_loop.start()
        if not self.voice_activity_loop.is_running():
            self.voice_activity_loop.start()

    def cog_unload(self) -> None:
        if self.scheduled_tasks_loop.is_running():
            self.scheduled_tasks_loop.cancel()
        if self.voice_activity_loop.is_running():
            self.voice_activity_loop.cancel()

    # =========================================================================
    # Activity Progression & Role Sync Engine
    # =========================================================================
    async def sync_member_reward_roles(
        self, guild: discord.Guild, member: discord.Member, act_row: Dict[str, Any]
    ) -> List[discord.Role]:
        """
        Evaluate all configured `role_rewards` for `guild` against `member`'s current
        level, messages_count, voice_minutes, and xp.
        Automatically assigns earned roles AND removes roles when requirements are no longer met
        (or when `stack_roles = 0` and a higher tier is achieved).
        """
        rewards = await self.bot.db.fetchall(
            "SELECT * FROM role_rewards WHERE guild_id = ? ORDER BY threshold ASC",
            (guild.id,),
        )
        if not rewards:
            return []

        newly_awarded: List[discord.Role] = []
        qualified_non_stack_by_type: Dict[str, int] = {}

        # Determine highest qualified threshold per reward_type for non-stacking rewards
        for rw in rewards:
            rtype = rw["reward_type"]
            val = int(act_row.get(rtype if rtype != "messages" else "messages_count", 0))
            if val >= int(rw["threshold"]):
                qualified_non_stack_by_type[rtype] = int(rw["role_id"])

        for rw in rewards:
            role = guild.get_role(int(rw["role_id"]))
            if role is None or (guild.me and role >= guild.me.top_role):
                continue

            rtype = rw["reward_type"]
            metric_col = "messages_count" if rtype == "messages" else rtype
            member_val = int(act_row.get(metric_col, 0))
            meets_threshold = member_val >= int(rw["threshold"])
            stack = bool(rw.get("stack_roles", 1))
            remove_on_drop = bool(rw.get("remove_on_drop", 1))

            should_have = meets_threshold
            if meets_threshold and not stack:
                # Only keep the highest threshold role of this reward_type
                if qualified_non_stack_by_type.get(rtype) != role.id:
                    should_have = False

            if should_have and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Cosmic Activity Progression ({rtype} >= {rw['threshold']})")
                    newly_awarded.append(role)
                except Exception:
                    pass
            elif not should_have and remove_on_drop and role in member.roles:
                try:
                    await member.remove_roles(
                        role,
                        reason=f"Cosmic Activity Requirement no longer met or upgraded ({rtype})",
                    )
                except Exception:
                    pass

        return newly_awarded

    async def add_member_xp(
        self,
        guild: discord.Guild,
        member: discord.Member,
        xp_delta: int,
        messages_delta: int = 0,
        voice_delta: int = 0,
        channel: Optional[discord.abc.Messageable] = None,
    ) -> Dict[str, Any]:
        """Add XP/activity points to a member, compute level ups, and sync role rewards."""
        now_iso = datetime.now(timezone.utc).isoformat()
        row = await self.bot.db.fetchone(
            "SELECT * FROM member_activity WHERE guild_id = ? AND user_id = ?",
            (guild.id, member.id),
        )
        old_level = int(row["level"]) if row else 0
        new_msgs = max(0, (int(row["messages_count"]) if row else 0) + messages_delta)
        new_voice = max(0, (int(row["voice_minutes"]) if row else 0) + voice_delta)
        new_xp = max(0, (int(row["xp"]) if row else 0) + xp_delta)
        new_level = level_from_xp(new_xp)

        if row is None:
            await self.bot.db.execute(
                """
                INSERT INTO member_activity (guild_id, user_id, messages_count, voice_minutes, xp, level, last_message_at, last_active_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild.id, member.id, new_msgs, new_voice, new_xp, new_level, now_iso, now_iso),
            )
        else:
            await self.bot.db.execute(
                """
                UPDATE member_activity
                SET messages_count = ?, voice_minutes = ?, xp = ?, level = ?, last_active_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (new_msgs, new_voice, new_xp, new_level, now_iso, guild.id, member.id),
            )

        updated_row = {
            "guild_id": guild.id,
            "user_id": member.id,
            "messages_count": new_msgs,
            "voice_minutes": new_voice,
            "xp": new_xp,
            "level": new_level,
        }
        awarded_roles = await self.sync_member_reward_roles(guild, member, updated_row)

        if new_level > old_level:
            guild_cfg = await self.bot.db.ensure_guild(guild.id)
            lvl_ch_id = guild_cfg.get("level_up_channel_id")
            target_ch = guild.get_channel(int(lvl_ch_id)) if lvl_ch_id else channel
            if isinstance(target_ch, discord.abc.Messageable):
                template = str(
                    guild_cfg.get("level_up_message")
                    or "GG {user.mention}! You advanced to **Level {level}** in **{server}**!"
                )
                text = render_template(
                    template,
                    member=member,
                    guild=guild,
                    extra={"level": new_level, "xp": new_xp},
                )
                if awarded_roles:
                    text += "\n🎖️ **Unlocked Role(s):** " + ", ".join(r.mention for r in awarded_roles)
                try:
                    await target_ch.send(embed=make_embed(title="🎉 Level Up!", description=text, color=THEME.SECONDARY))
                except Exception:
                    pass

        return updated_row

    # =========================================================================
    # Message Listener (Anti-Spam + Activity XP + Event Rules)
    # =========================================================================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return

        guild = message.guild
        member = message.author
        cfg = await self.bot.db.ensure_guild(guild.id)

        # 1. Anti-Spam Protection
        if cfg.get("anti_spam_enabled", 1):
            now = time.monotonic()
            window = max(2, int(cfg.get("anti_spam_window_seconds") or 5))
            max_msgs = max(3, int(cfg.get("anti_spam_max_messages") or 6))
            bucket = self._spam_buckets[(guild.id, member.id)]
            bucket.append(now)
            while bucket and bucket[0] < now - window:
                bucket.popleft()
            if len(bucket) > max_msgs and not self.bot.permissions_engine.is_emergency_admin(member):
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        # 2. Message XP & Activity Tracking
        if cfg.get("xp_enabled", 1):
            ignored_chs = set(json.loads(cfg.get("xp_ignored_channels_json") or "[]"))
            ignored_roles = set(json.loads(cfg.get("xp_ignored_roles_json") or "[]"))
            member_role_ids = {r.id for r in member.roles}
            if message.channel.id not in ignored_chs and not (member_role_ids & ignored_roles):
                now = time.monotonic()
                cd = int(cfg.get("xp_cooldown_seconds") or 60)
                last_xp = self._xp_cooldowns.get((guild.id, member.id), 0.0)
                if now - last_xp >= cd:
                    self._xp_cooldowns[(guild.id, member.id)] = now
                    xp_gain = random.randint(
                        int(cfg.get("xp_per_message_min") or 15),
                        max(int(cfg.get("xp_per_message_min") or 15), int(cfg.get("xp_per_message_max") or 25)),
                    )
                    updated = await self.add_member_xp(
                        guild, member, xp_delta=xp_gain, messages_delta=1, channel=message.channel
                    )
                    await self._check_event_rules(
                        guild,
                        "message_milestone",
                        member=member,
                        extra={"messages_count": updated["messages_count"]},
                    )
                else:
                    # Still count the message even during XP cooldown
                    await self.add_member_xp(guild, member, xp_delta=0, messages_delta=1)

    @tasks.loop(minutes=1)
    async def voice_activity_loop(self) -> None:
        """Award voice activity minutes and XP to active, non-deafened members in voice channels."""
        for guild in self.bot.guilds:
            cfg = await self.bot.db.ensure_guild(guild.id)
            if not cfg.get("xp_enabled", 1):
                continue
            xp_per_min = int(cfg.get("xp_per_voice_minute") or 10)
            for vc in guild.voice_channels:
                if guild.afk_channel and vc.id == guild.afk_channel.id:
                    continue
                human_members = [
                    m for m in vc.members if not m.bot and not (m.voice and (m.voice.self_deaf or m.voice.deaf))
                ]
                if len(human_members) < 1:
                    continue
                for member in human_members:
                    await self.add_member_xp(
                        guild, member, xp_delta=xp_per_min, voice_delta=1
                    )

    @tasks.loop(seconds=30)
    async def scheduled_tasks_loop(self) -> None:
        """Execute due one-time scheduled messages, repeating announcements, and inactivity role pruning."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        due_rows = await self.bot.db.fetchall(
            "SELECT * FROM scheduled_automations WHERE enabled = 1 AND next_run_at <= ?",
            (now_iso,),
        )
        for row in due_rows:
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is None:
                continue

            atype = row["automation_type"]
            channel = guild.get_channel(int(row["channel_id"])) if row.get("channel_id") else None

            if atype in ("scheduled_once", "repeating_message") and isinstance(channel, discord.TextChannel):
                embed_obj = None
                if row.get("embed_name"):
                    saved = await self.bot.db.fetchone(
                        "SELECT embed_json FROM saved_embeds WHERE guild_id = ? AND name = ?",
                        (guild.id, row["embed_name"]),
                    )
                    if saved:
                        embed_obj = embed_from_dict(json.loads(saved["embed_json"]), guild=guild)
                content = render_template(row.get("message_text") or "", guild=guild) or None
                try:
                    await channel.send(content=content, embed=embed_obj)
                except Exception:
                    pass

            if atype == "scheduled_once":
                await self.bot.db.execute(
                    "UPDATE scheduled_automations SET enabled = 0, last_run_at = ? WHERE id = ?",
                    (now_iso, row["id"]),
                )
            else:
                interval = max(60, int(row.get("interval_seconds") or 3600))
                next_run = (now + timedelta(seconds=interval)).isoformat()
                await self.bot.db.execute(
                    "UPDATE scheduled_automations SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                    (now_iso, next_run, row["id"]),
                )

    # =========================================================================
    # Event-Driven Automation Rules (Join, Reaction, Channel Management)
    # =========================================================================
    async def _check_event_rules(
        self,
        guild: discord.Guild,
        trigger_event: str,
        member: Optional[discord.Member] = None,
        channel: Optional[discord.abc.GuildChannel] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        rules = await self.bot.db.fetchall(
            "SELECT * FROM automation_rules WHERE guild_id = ? AND trigger_event = ? AND enabled = 1",
            (guild.id, trigger_event),
        )
        for rule in rules:
            cond = json.loads(rule.get("conditions_json") or "{}")
            acts = json.loads(rule.get("actions_json") or "{}")

            if trigger_event == "message_milestone" and extra:
                req_msgs = int(cond.get("messages_count") or 0)
                if req_msgs and extra.get("messages_count") != req_msgs:
                    continue

            if trigger_event == "reaction_add" and extra:
                req_emoji = str(cond.get("emoji") or "")
                if req_emoji and str(extra.get("emoji")) != req_emoji:
                    continue

            if member and acts.get("assign_role"):
                role = guild.get_role(int(acts["assign_role"]))
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role, reason=f"Automation Rule: {rule['name']}")
                    except Exception:
                        pass

            if member and acts.get("remove_role"):
                role = guild.get_role(int(acts["remove_role"]))
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason=f"Automation Rule: {rule['name']}")
                    except Exception:
                        pass

            if acts.get("send_channel_id") and acts.get("message"):
                ch = guild.get_channel(int(acts["send_channel_id"]))
                if isinstance(ch, discord.TextChannel):
                    msg_txt = render_template(str(acts["message"]), member=member, guild=guild)
                    try:
                        await ch.send(msg_txt)
                    except Exception:
                        pass

            if trigger_event == "channel_create" and isinstance(channel, discord.TextChannel):
                if acts.get("slowmode") is not None:
                    try:
                        await channel.edit(slowmode_delay=int(acts["slowmode"]))
                    except Exception:
                        pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.member is None or payload.member.bot:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild:
            await self._check_event_rules(
                guild,
                "reaction_add",
                member=payload.member,
                extra={"emoji": str(payload.emoji), "channel_id": payload.channel_id},
            )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._check_event_rules(channel.guild, "channel_create", channel=channel)

    # =========================================================================
    # /rank & /leaderboard Commands
    # =========================================================================
    @commands.hybrid_command(
        name="rank",
        description="View your (or another member's) activity XP, level, and progression.",
    )
    @app_commands.describe(member="Member to inspect (defaults to yourself)")
    async def rank_cmd(
        self, ctx: commands.Context[Any], member: Optional[discord.Member] = None
    ) -> None:
        if ctx.guild is None:
            return
        target = member or (ctx.author if isinstance(ctx.author, discord.Member) else None)
        if target is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM member_activity WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, target.id),
        )
        xp = int(row["xp"]) if row else 0
        lvl = int(row["level"]) if row else 0
        msgs = int(row["messages_count"]) if row else 0
        voice = int(row["voice_minutes"]) if row else 0
        next_lvl_xp = xp_for_level(lvl + 1)
        cur_lvl_floor = xp_for_level(lvl)
        progress = build_progress_bar(xp - cur_lvl_floor, max(1, next_lvl_xp - cur_lvl_floor))

        embed = make_embed(
            title=f"🌟 Activity Rank — {target.display_name}",
            description=f"**Level {lvl}** • `{xp:,} / {next_lvl_xp:,} XP`\n{progress}",
            color=THEME.PRIMARY,
            thumbnail_url=target.display_avatar.url if target.display_avatar else None,
        )
        embed.add_field(name="Messages Sent", value=f"`{msgs:,}`", inline=True)
        embed.add_field(name="Voice Activity", value=f"`{voice:,} mins`", inline=True)
        await ctx.reply(embed=embed)

    @commands.hybrid_command(
        name="leaderboard",
        description="Display the Side Quest Studio activity and level leaderboard.",
    )
    async def leaderboard_cmd(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM member_activity WHERE guild_id = ? ORDER BY xp DESC LIMIT 15",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(embed=info_embed("Leaderboard", "No activity recorded yet!"))
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for idx, r in enumerate(rows, start=1):
            badge = medals[idx - 1] if idx <= 3 else f"`#{idx:02d}`"
            lines.append(
                f"{badge} <@{r['user_id']}> — **Level {r['level']}** (`{r['xp']:,} XP` • `{r['messages_count']}` msgs • `{r['voice_minutes']}m` voice)"
            )
        await ctx.reply(
            embed=make_embed(
                title=f"🏆 Activity Leaderboard — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.PRIMARY,
            )
        )

    # =========================================================================
    # /activity & /automation Configuration Commands
    # =========================================================================
    @commands.hybrid_group(
        name="activity",
        description="Configure activity XP settings, level-up messages, and automatic role rewards.",
        invoke_without_command=True,
    )
    async def activity_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_guild(ctx.guild.id)
        rewards = await self.bot.db.fetchall(
            "SELECT * FROM role_rewards WHERE guild_id = ? ORDER BY reward_type, threshold ASC",
            (ctx.guild.id,),
        )
        embed = make_embed(
            title=f"📈 Activity & Role Progression — {ctx.guild.name}",
            description=(
                f"**XP Tracking:** {'✅ Enabled' if cfg.get('xp_enabled') else '❌ Disabled'}\n"
                f"**Message XP:** `{cfg.get('xp_per_message_min')}–{cfg.get('xp_per_message_max')} XP` (Cooldown: `{cfg.get('xp_cooldown_seconds')}s`)\n"
                f"**Voice XP:** `{cfg.get('xp_per_voice_minute')} XP/min`\n"
                f"**Anti-Spam:** `{'Enabled' if cfg.get('anti_spam_enabled') else 'Disabled'}` ({cfg.get('anti_spam_max_messages')} msgs / {cfg.get('anti_spam_window_seconds')}s)"
            ),
            color=THEME.PRIMARY,
        )
        if rewards:
            rw_lines = [
                f"• **{r['reward_type'].upper()} >= {r['threshold']}** → <@&{r['role_id']}> "
                f"(Stack: `{'Yes' if r['stack_roles'] else 'No'}` • Auto-Remove on Drop: `{'Yes' if r['remove_on_drop'] else 'No'}`)"
                for r in rewards
            ]
            embed.add_field(name="🎖️ Role Rewards", value="\n".join(rw_lines), inline=False)
        else:
            embed.add_field(name="🎖️ Role Rewards", value="*None configured yet.*", inline=False)
        await ctx.reply(embed=embed)

    @activity_group.command(
        name="config",
        description="Update message XP, voice XP, cooldown, and level-up announcement channel.",
    )
    @app_commands.describe(
        xp_enabled="Enable or disable XP progression",
        min_xp="Minimum XP per message",
        max_xp="Maximum XP per message",
        voice_xp="XP per minute in voice",
        cooldown="Cooldown in seconds between XP-earning messages",
        level_up_channel="Optional channel for level-up announcements",
    )
    async def activity_config(
        self,
        ctx: commands.Context[Any],
        xp_enabled: bool = True,
        min_xp: int = 15,
        max_xp: int = 25,
        voice_xp: int = 10,
        cooldown: int = 60,
        level_up_channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.update_guild_config(
            ctx.guild.id,
            xp_enabled=int(xp_enabled),
            xp_per_message_min=max(1, min_xp),
            xp_per_message_max=max(min_xp, max_xp),
            xp_per_voice_minute=max(0, voice_xp),
            xp_cooldown_seconds=max(5, cooldown),
            level_up_channel_id=level_up_channel.id if level_up_channel else None,
        )
        await ctx.reply(
            embed=success_embed(
                "Activity Settings Updated",
                f"Message XP: `{min_xp}–{max_xp}` • Voice XP: `{voice_xp}/min` • Cooldown: `{cooldown}s`.",
            )
        )

    @activity_group.command(
        name="reward_add",
        description="Add a level-based or activity-milestone role reward.",
    )
    @app_commands.describe(
        role="Role to award",
        reward_type="Milestone type: level, messages, voice_minutes, or xp",
        threshold="Required level or milestone number",
        stack_roles="Keep previous tier roles when advancing (True/False)",
        remove_on_drop="Automatically remove role if member drops below requirement (True/False)",
    )
    async def activity_reward_add(
        self,
        ctx: commands.Context[Any],
        role: discord.Role,
        reward_type: str,
        threshold: int,
        stack_roles: bool = True,
        remove_on_drop: bool = True,
    ) -> None:
        if ctx.guild is None:
            return
        rtype = reward_type.lower().strip()
        if rtype not in ("level", "messages", "voice_minutes", "xp"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Type",
                    "Reward type must be `level`, `messages`, `voice_minutes`, or `xp`.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO role_rewards (guild_id, role_id, reward_type, threshold, remove_on_drop, stack_roles)
            VALUES (?, ?, ?, ?, ?, ?)
            """
            if not self.bot.db.is_postgres
            else """
            INSERT INTO role_rewards (guild_id, role_id, reward_type, threshold, remove_on_drop, stack_roles)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, role_id, reward_type) DO UPDATE SET threshold = EXCLUDED.threshold, remove_on_drop = EXCLUDED.remove_on_drop, stack_roles = EXCLUDED.stack_roles
            """,
            (ctx.guild.id, role.id, rtype, max(1, threshold), int(remove_on_drop), int(stack_roles)),
        )
        await ctx.reply(
            embed=success_embed(
                "Activity Role Reward Added",
                f"Members reaching **{rtype.upper()} >= {threshold}** will automatically receive {role.mention}.",
            )
        )

    @activity_group.command(
        name="reward_remove", description="Remove a configured activity role reward."
    )
    async def activity_reward_remove(
        self, ctx: commands.Context[Any], role: discord.Role
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.execute(
            "DELETE FROM role_rewards WHERE guild_id = ? AND role_id = ?",
            (ctx.guild.id, role.id),
        )
        await ctx.reply(
            embed=success_embed("Role Reward Removed", f"Removed activity reward for {role.mention}.")
        )

    # =========================================================================
    # /automation Group (Scheduled, Repeating & Event Rules)
    # =========================================================================
    @commands.hybrid_group(
        name="automation",
        description="Manage scheduled messages, repeating announcements, and event-triggered server workflows.",
        invoke_without_command=True,
    )
    async def automation_group(self, ctx: commands.Context[Any]) -> None:
        await self.automation_list(ctx)

    @automation_group.command(
        name="schedule",
        description="Create a scheduled message or repeating announcement.",
    )
    @app_commands.describe(
        name="Short identifier for this automation",
        channel="Target channel for the message",
        interval="Interval (e.g. 30m, 6h, 1d) or delay for one-time message",
        repeating="True for repeating announcement, False for one-time scheduled message",
        message="Message text (supports {server} and {member_count})",
    )
    async def automation_schedule(
        self,
        ctx: commands.Context[Any],
        name: str,
        channel: discord.TextChannel,
        interval: str,
        repeating: bool,
        *,
        message: str,
    ) -> None:
        if ctx.guild is None:
            return
        seconds = parse_duration(interval)
        if not seconds or seconds < 60:
            await ctx.reply(
                embed=error_embed("Invalid Interval", "Interval must be at least `60s` (e.g. `15m`, `2h`, `1d`)."),
                ephemeral=True,
            )
            return
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(seconds=seconds)).isoformat()
        atype = "repeating_message" if repeating else "scheduled_once"
        await self.bot.db.execute(
            """
            INSERT INTO scheduled_automations (
                guild_id, name, automation_type, channel_id, message_text,
                interval_seconds, next_run_at, enabled, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                ctx.guild.id,
                name.strip()[:50],
                atype,
                channel.id,
                message,
                seconds,
                next_run,
                ctx.author.id,
                now.isoformat(),
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Scheduled Automation Created",
                f"**Name:** `{name}`\n"
                f"**Type:** {'Repeating Announcement' if repeating else 'One-Time Scheduled Message'}\n"
                f"**Channel:** {channel.mention}\n"
                f"**Cadence:** Every **{format_duration(seconds)}**",
            )
        )

    @automation_group.command(
        name="rule_add",
        description="Create an event-driven automation workflow (join/reaction/milestone/channel_create).",
    )
    @app_commands.describe(
        name="Unique rule name",
        trigger_event="Event: member_join, reaction_add, message_milestone, channel_create",
        assign_role="Optional role to assign when triggered",
        send_channel="Optional channel to send an automated message in",
        message="Optional message template to send",
    )
    async def automation_rule_add(
        self,
        ctx: commands.Context[Any],
        name: str,
        trigger_event: str,
        assign_role: Optional[discord.Role] = None,
        send_channel: Optional[discord.TextChannel] = None,
        *,
        message: Optional[str] = None,
    ) -> None:
        if ctx.guild is None:
            return
        ev = trigger_event.lower().strip()
        if ev not in ("member_join", "member_leave", "reaction_add", "message_milestone", "channel_create"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Event",
                    "Choose `member_join`, `member_leave`, `reaction_add`, `message_milestone`, or `channel_create`.",
                ),
                ephemeral=True,
            )
            return
        actions: Dict[str, Any] = {}
        if assign_role:
            actions["assign_role"] = assign_role.id
        if send_channel:
            actions["send_channel_id"] = send_channel.id
        if message:
            actions["message"] = message

        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO automation_rules (guild_id, name, trigger_event, conditions_json, actions_json, enabled, created_at)
            VALUES (?, ?, ?, '{}', ?, 1, ?)
            """
            if not self.bot.db.is_postgres
            else """
            INSERT INTO automation_rules (guild_id, name, trigger_event, conditions_json, actions_json, enabled, created_at)
            VALUES (?, ?, ?, '{}', ?, 1, ?)
            ON CONFLICT (guild_id, name) DO UPDATE SET trigger_event = EXCLUDED.trigger_event, actions_json = EXCLUDED.actions_json, enabled = 1
            """,
            (
                ctx.guild.id,
                name.lower().strip()[:40],
                ev,
                json.dumps(actions),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Automation Rule Saved",
                f"Rule **`{name}`** will now trigger on `{ev}`.",
            )
        )

    @automation_group.command(
        name="list", description="List all scheduled automations and event workflow rules."
    )
    async def automation_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        sched = await self.bot.db.fetchall(
            "SELECT * FROM scheduled_automations WHERE guild_id = ? ORDER BY id DESC",
            (ctx.guild.id,),
        )
        rules = await self.bot.db.fetchall(
            "SELECT * FROM automation_rules WHERE guild_id = ? ORDER BY id DESC",
            (ctx.guild.id,),
        )
        embed = make_embed(
            title=f"⚙️ Server Automations — {ctx.guild.name}",
            color=THEME.PRIMARY,
        )
        s_lines = [
            f"• `#{s['id']}` **{s['name']}** ({s['automation_type']}) → <#{s['channel_id']}> "
            f"[{format_duration(s['interval_seconds'])}] {'✅' if s['enabled'] else '❌'}"
            for s in sched[:10]
        ]
        r_lines = [
            f"• **{r['name']}** (`{r['trigger_event']}`) {'✅' if r['enabled'] else '❌'}"
            for r in rules[:10]
        ]
        embed.add_field(
            name=f"⏰ Scheduled & Repeating ({len(sched)})",
            value="\n".join(s_lines) or "*None*",
            inline=False,
        )
        embed.add_field(
            name=f"⚡ Event Workflow Rules ({len(rules)})",
            value="\n".join(r_lines) or "*None*",
            inline=False,
        )
        await ctx.reply(embed=embed)

    @automation_group.command(
        name="delete", description="Delete a scheduled automation by ID or event rule by name."
    )
    @app_commands.describe(target="Automation ID number or Rule name")
    async def automation_delete(self, ctx: commands.Context[Any], target: str) -> None:
        if ctx.guild is None:
            return
        if target.isdigit():
            await self.bot.db.execute(
                "DELETE FROM scheduled_automations WHERE guild_id = ? AND id = ?",
                (ctx.guild.id, int(target)),
            )
        else:
            await self.bot.db.execute(
                "DELETE FROM automation_rules WHERE guild_id = ? AND name = ?",
                (ctx.guild.id, target.lower().strip()),
            )
        await ctx.reply(
            embed=success_embed("Automation Deleted", f"Removed automation `{target}`.")
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(AutomationCog(bot))
