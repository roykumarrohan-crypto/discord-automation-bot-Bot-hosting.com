"""
Cosmic ki baddie — Anti-Nuke Detection & Quarantine Engine (`cogs/antinuke.py`)
Monitors and protects Side Quest Studio against:
- Mass channel deletion & creation
- Mass role deletion & creation
- Mass member bans & kicks
- Suspicious webhook creations
- Unauthorized/unverified bot additions
- Suspicious administrative permission grants
Includes false-positive protection (never punishes on a single isolated event),
audit-log actor+target verification, trusted user/role lists, and incident logging.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed
from utils.security import (
    SlidingWindowTracker,
    adjust_threshold_for_mode,
    is_trusted_actor,
)

if TYPE_CHECKING:
    from main import CosmicBot


class AntiNukeCog(commands.Cog, name="AntiNuke"):
    """Real-time anti-nuke monitoring, audit verification, and automated remediation."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot
        self.tracker = SlidingWindowTracker()

    async def _verify_audit_actor(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int,
    ) -> Optional[discord.Member]:
        """
        Verify the responsible actor AND target in the Discord Audit Log within the last 12 seconds.
        Returns a `discord.Member` only if verified.
        """
        if not guild.me or not guild.me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age > 12:
                    continue
                if getattr(entry.target, "id", None) == target_id and entry.user:
                    return guild.get_member(entry.user.id)
        except Exception:
            return None
        return None

    async def _handle_security_event(
        self,
        guild: discord.Guild,
        actor: Optional[discord.Member],
        incident_type: str,
        threshold_column: str,
        details: str,
    ) -> None:
        """
        Evaluate a verified security event against sliding-window thresholds and execute
        configured remediation if the actor is not trusted and exceeds the multi-event threshold.
        """
        if actor is None:
            return

        an_cfg = await self.bot.db.ensure_antinuke_config(guild.id)
        if not an_cfg.get("enabled", 1):
            return

        guild_cfg = await self.bot.db.ensure_guild(guild.id)
        protection_mode = str(guild_cfg.get("protection_mode") or "normal")
        trusted_users = json.loads(an_cfg.get("trusted_users_json") or "[]")
        trusted_roles = json.loads(an_cfg.get("trusted_roles_json") or "[]")

        actor_role_ids = [r.id for r in actor.roles]
        if is_trusted_actor(
            actor_id=actor.id,
            actor_role_ids=actor_role_ids,
            guild_owner_id=guild.owner_id or 0,
            bot_user_id=self.bot.user.id if self.bot.user else 0,
            trusted_users=trusted_users,
            trusted_roles=trusted_roles,
        ):
            return

        window = int(an_cfg.get("window_seconds") or 30)
        base_threshold = int(an_cfg.get(threshold_column) or 3)
        effective_threshold = adjust_threshold_for_mode(base_threshold, protection_mode)

        count = self.tracker.record_and_count(guild.id, actor.id, incident_type, window)
        # False-positive safeguard: never trigger automated punishment on a single isolated event
        if count < max(2, effective_threshold):
            return

        self.tracker.reset_actor(guild.id, actor.id)
        punishment = str(an_cfg.get("punishment_action") or "quarantine").lower()
        action_taken = await self._remediate_actor(guild, actor, punishment, incident_type)

        # Auto-activate Safe Mode if configured
        if an_cfg.get("auto_safe_mode_on_trigger", 1):
            await self.bot.db.update_guild_config(
                guild.id,
                safe_mode=1,
                safe_mode_activated_by=self.bot.user.id if self.bot.user else 0,
                safe_mode_activated_at=datetime.now(timezone.utc).isoformat(),
            )
            action_taken += " + Safe Mode Activated"

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.bot.db.execute(
            """
            INSERT INTO security_incidents (
                guild_id, actor_id, incident_type, event_count, threshold, action_taken, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild.id,
                actor.id,
                incident_type,
                count,
                effective_threshold,
                action_taken,
                details,
                now_iso,
            ),
        )
        await self._send_security_alert(
            guild=guild,
            actor=actor,
            incident_type=incident_type,
            count=count,
            threshold=effective_threshold,
            action_taken=action_taken,
            details=details,
        )

    async def _remediate_actor(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        punishment: str,
        incident_type: str,
    ) -> str:
        """Execute the configured anti-nuke response while respecting Discord role hierarchy."""
        if guild.me and actor.top_role >= guild.me.top_role:
            return "Alerted Only (Actor role >= Bot top role)"

        reason = f"Cosmic Anti-Nuke Protection: {incident_type} threshold exceeded"
        try:
            if punishment in ("quarantine", "strip_roles"):
                removable = [
                    r
                    for r in actor.roles
                    if r.id != guild.default_role.id
                    and not r.managed
                    and (not guild.me or r < guild.me.top_role)
                ]
                if removable:
                    await actor.remove_roles(*removable, reason=reason)
                return f"Quarantined (Stripped {len(removable)} roles)"
            if punishment == "kick":
                await actor.kick(reason=reason)
                return "Kicked from server"
            if punishment == "ban":
                await guild.ban(actor, reason=reason, delete_message_days=0)
                return "Banned from server"
        except Exception as exc:
            return f"Remediation failed ({exc})"
        return "Alerted Security Staff"

    async def _send_security_alert(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        incident_type: str,
        count: int,
        threshold: int,
        action_taken: str,
        details: str,
    ) -> None:
        guild_cfg = await self.bot.db.ensure_guild(guild.id)
        alert_ch_id = guild_cfg.get("security_alert_channel_id") or guild_cfg.get("modlog_channel_id")
        channel = guild.get_channel(int(alert_ch_id)) if alert_ch_id else None
        if not isinstance(channel, discord.TextChannel):
            return

        embed = make_embed(
            title=f"🚨 ANTI-NUKE SECURITY ALERT — {incident_type.upper()}",
            description=details,
            color=THEME.DANGER,
        )
        embed.add_field(name="Responsible Actor", value=f"{actor.mention} (`{actor.id}`)", inline=True)
        embed.add_field(name="Burst Count", value=f"`{count} / {threshold}`", inline=True)
        embed.add_field(name="Automated Response", value=f"**{action_taken}**", inline=False)
        try:
            admin_role_id = guild_cfg.get("admin_role_id")
            ping = f"<@&{admin_role_id}>" if admin_role_id else f"<@{guild.owner_id}>"
            await channel.send(content=f"🚨 {ping}", embed=embed)
        except Exception:
            pass

    # =========================================================================
    # Anti-Nuke Event Listeners
    # =========================================================================
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._verify_audit_actor(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id
        )
        await self._handle_security_event(
            channel.guild,
            actor,
            "mass_channel_delete",
            "max_channel_delete",
            f"Rapid deletion of channel `#{channel.name}` (`{channel.id}`).",
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._verify_audit_actor(
            channel.guild, discord.AuditLogAction.channel_create, channel.id
        )
        await self._handle_security_event(
            channel.guild,
            actor,
            "mass_channel_create",
            "max_channel_create",
            f"Rapid creation of channel `#{channel.name}` (`{channel.id}`).",
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        actor = await self._verify_audit_actor(
            role.guild, discord.AuditLogAction.role_delete, role.id
        )
        await self._handle_security_event(
            role.guild,
            actor,
            "mass_role_delete",
            "max_role_delete",
            f"Rapid deletion of role `@{role.name}` (`{role.id}`).",
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        actor = await self._verify_audit_actor(
            role.guild, discord.AuditLogAction.role_create, role.id
        )
        await self._handle_security_event(
            role.guild,
            actor,
            "mass_role_create",
            "max_role_create",
            f"Rapid creation of role `@{role.name}` (`{role.id}`).",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        actor = await self._verify_audit_actor(guild, discord.AuditLogAction.ban, user.id)
        await self._handle_security_event(
            guild,
            actor,
            "mass_ban",
            "max_bans",
            f"Rapid ban of user `{user}` (`{user.id}`).",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        actor = await self._verify_audit_actor(
            member.guild, discord.AuditLogAction.kick, member.id
        )
        if actor:
            await self._handle_security_event(
                member.guild,
                actor,
                "mass_kick",
                "max_kicks",
                f"Rapid kick of member `{member}` (`{member.id}`).",
            )

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        actor = await self._verify_audit_actor(
            channel.guild, discord.AuditLogAction.webhook_create, channel.id
        )
        if actor:
            await self._handle_security_event(
                channel.guild,
                actor,
                "mass_webhook_create",
                "max_webhooks",
                f"Suspicious webhook creation in <#{channel.id}>.",
            )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Detect suspicious unverified bot additions."""
        if not member.bot:
            return
        an_cfg = await self.bot.db.ensure_antinuke_config(member.guild.id)
        if not an_cfg.get("enabled", 1) or not an_cfg.get("block_unverified_bots", 1):
            return
        actor = await self._verify_audit_actor(
            member.guild, discord.AuditLogAction.bot_add, member.id
        )
        if actor is None:
            return
        trusted_users = json.loads(an_cfg.get("trusted_users_json") or "[]")
        trusted_roles = json.loads(an_cfg.get("trusted_roles_json") or "[]")
        if is_trusted_actor(
            actor.id,
            [r.id for r in actor.roles],
            member.guild.owner_id or 0,
            self.bot.user.id if self.bot.user else 0,
            trusted_users,
            trusted_roles,
        ):
            return

        is_verified = member.public_flags.verified_bot if member.public_flags else False
        if not is_verified:
            try:
                await member.kick(reason=f"Cosmic Anti-Nuke: Unverified bot added by untrusted user {actor}")
                await self._send_security_alert(
                    member.guild,
                    actor,
                    "unverified_bot_addition",
                    1,
                    1,
                    f"Kicked unverified bot {member}",
                    f"{actor.mention} added unverified bot {member.mention} (`{member.id}`).",
                )
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        """Detect suspicious escalation of a role to Administrator permissions."""
        if not before.permissions.administrator and after.permissions.administrator:
            actor = await self._verify_audit_actor(
                after.guild, discord.AuditLogAction.role_update, after.id
            )
            await self._handle_security_event(
                after.guild,
                actor,
                "suspicious_admin_grant",
                "max_role_create",
                f"Role {after.mention} was granted `Administrator` permissions.",
            )

    # =========================================================================
    # /antinuke Configuration Commands
    # =========================================================================
    @commands.hybrid_group(
        name="antinuke",
        description="Configure anti-nuke thresholds, trusted users/roles, and automated protection.",
        invoke_without_command=True,
    )
    async def antinuke_group(self, ctx: commands.Context[Any]) -> None:
        await self.antinuke_view(ctx)

    @antinuke_group.command(
        name="view", description="Display current anti-nuke thresholds and trusted entities."
    )
    async def antinuke_view(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_antinuke_config(ctx.guild.id)
        t_users = json.loads(cfg.get("trusted_users_json") or "[]")
        t_roles = json.loads(cfg.get("trusted_roles_json") or "[]")
        embed = make_embed(
            title=f"🛡️ Anti-Nuke Protection — {ctx.guild.name}",
            description=(
                f"**Status:** {'✅ Active' if cfg.get('enabled') else '❌ Disabled'}\n"
                f"**Time Window:** `{cfg.get('window_seconds')}s`\n"
                f"**Punishment Action:** `{str(cfg.get('punishment_action')).upper()}`\n"
                f"**Auto Safe-Mode on Trigger:** `{'Yes' if cfg.get('auto_safe_mode_on_trigger') else 'No'}`"
            ),
            color=THEME.PRIMARY,
        )
        thresh_text = (
            f"• **Channel Delete:** `{cfg['max_channel_delete']}` | **Channel Create:** `{cfg['max_channel_create']}`\n"
            f"• **Role Delete:** `{cfg['max_role_delete']}` | **Role Create:** `{cfg['max_role_create']}`\n"
            f"• **Mass Bans:** `{cfg['max_bans']}` | **Mass Kicks:** `{cfg['max_kicks']}`\n"
            f"• **Webhooks:** `{cfg['max_webhooks']}` | **Block Unverified Bots:** `{'Yes' if cfg['block_unverified_bots'] else 'No'}`"
        )
        embed.add_field(name="⚡ Detection Thresholds", value=thresh_text, inline=False)
        embed.add_field(
            name=f"🤝 Trusted Users ({len(t_users)})",
            value=", ".join(f"<@{u}>" for u in t_users) or "*Server Owner Only*",
            inline=True,
        )
        embed.add_field(
            name=f"🎭 Trusted Roles ({len(t_roles)})",
            value=", ".join(f"<@&{r}>" for r in t_roles) or "*None*",
            inline=True,
        )
        await ctx.reply(embed=embed)

    @antinuke_group.command(
        name="threshold",
        description="Configure anti-nuke detection thresholds and sliding time window.",
    )
    @app_commands.describe(
        window_seconds="Sliding detection window in seconds (10-120)",
        max_channel_delete="Max channel deletions within window (min 2)",
        max_role_delete="Max role deletions within window (min 2)",
        max_bans="Max bans within window (min 2)",
        max_kicks="Max kicks within window (min 2)",
    )
    async def antinuke_threshold(
        self,
        ctx: commands.Context[Any],
        window_seconds: int = 30,
        max_channel_delete: int = 3,
        max_role_delete: int = 3,
        max_bans: int = 3,
        max_kicks: int = 4,
    ) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.ensure_antinuke_config(ctx.guild.id)
        await self.bot.db.execute(
            """
            UPDATE antinuke_configs
            SET window_seconds = ?, max_channel_delete = ?, max_role_delete = ?,
                max_bans = ?, max_kicks = ?
            WHERE guild_id = ?
            """,
            (
                max(10, min(120, window_seconds)),
                max(2, max_channel_delete),
                max(2, max_role_delete),
                max(2, max_bans),
                max(2, max_kicks),
                ctx.guild.id,
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Anti-Nuke Thresholds Updated",
                f"Window: `{window_seconds}s` • Channel Del: `{max(2, max_channel_delete)}` • "
                f"Role Del: `{max(2, max_role_delete)}` • Bans: `{max(2, max_bans)}` • Kicks: `{max(2, max_kicks)}`.",
            )
        )

    @antinuke_group.command(
        name="punishment",
        description="Set the automated response when an anti-nuke threshold is breached.",
    )
    @app_commands.describe(
        action="Action: quarantine (strip roles), kick, ban, or alert_only",
        auto_safe_mode="Automatically engage Server Safe Mode when triggered",
    )
    async def antinuke_punishment(
        self,
        ctx: commands.Context[Any],
        action: str,
        auto_safe_mode: bool = True,
    ) -> None:
        if ctx.guild is None:
            return
        act = action.lower().strip()
        if act not in ("quarantine", "strip_roles", "kick", "ban", "alert_only"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Action",
                    "Choose `quarantine`, `kick`, `ban`, or `alert_only`.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.ensure_antinuke_config(ctx.guild.id)
        await self.bot.db.execute(
            "UPDATE antinuke_configs SET punishment_action = ?, auto_safe_mode_on_trigger = ? WHERE guild_id = ?",
            (act, int(auto_safe_mode), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Anti-Nuke Response Updated",
                f"**Punishment:** `{act.upper()}`\n**Auto Safe Mode:** `{'Enabled' if auto_safe_mode else 'Disabled'}`",
            )
        )

    @antinuke_group.command(
        name="trust_user",
        description="Add or remove a trusted user from the anti-nuke bypass list (Server Owner / Admin only).",
    )
    @app_commands.describe(user="User to toggle in the trusted list")
    async def antinuke_trust_user(
        self, ctx: commands.Context[Any], user: discord.User
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not self.bot.permissions_engine.is_emergency_admin(ctx.author):
            await ctx.reply(
                embed=error_embed("Admin Only", "Only the Server Owner or Administrators can modify the trusted list."),
                ephemeral=True,
            )
            return
        cfg = await self.bot.db.ensure_antinuke_config(ctx.guild.id)
        trusted: List[int] = json.loads(cfg.get("trusted_users_json") or "[]")
        if user.id in trusted:
            trusted.remove(user.id)
            verb = "Removed from"
        else:
            trusted.append(user.id)
            verb = "Added to"
        await self.bot.db.execute(
            "UPDATE antinuke_configs SET trusted_users_json = ? WHERE guild_id = ?",
            (json.dumps(trusted), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Trusted Users Updated", f"{verb} trusted anti-nuke bypass list: {user.mention}."
            )
        )

    @antinuke_group.command(
        name="trust_role",
        description="Add or remove a trusted role from the anti-nuke bypass list.",
    )
    @app_commands.describe(role="Role to toggle in the trusted list")
    async def antinuke_trust_role(
        self, ctx: commands.Context[Any], role: discord.Role
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if not self.bot.permissions_engine.is_emergency_admin(ctx.author):
            await ctx.reply(
                embed=error_embed("Admin Only", "Only the Server Owner or Administrators can modify the trusted list."),
                ephemeral=True,
            )
            return
        cfg = await self.bot.db.ensure_antinuke_config(ctx.guild.id)
        trusted: List[int] = json.loads(cfg.get("trusted_roles_json") or "[]")
        if role.id in trusted:
            trusted.remove(role.id)
            verb = "Removed from"
        else:
            trusted.append(role.id)
            verb = "Added to"
        await self.bot.db.execute(
            "UPDATE antinuke_configs SET trusted_roles_json = ? WHERE guild_id = ?",
            (json.dumps(trusted), ctx.guild.id),
        )
        await ctx.reply(
            embed=success_embed(
                "Trusted Roles Updated", f"{verb} trusted anti-nuke bypass list: {role.mention}."
            )
        )

    @antinuke_group.command(
        name="incidents", description="View recent anti-nuke security incidents in this server."
    )
    async def antinuke_incidents(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM security_incidents WHERE guild_id = ? ORDER BY id DESC LIMIT 15",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed("Security Incidents", "✨ Zero security incidents recorded.")
            )
            return
        lines = [
            f"• **`{r['incident_type']}`** by <@{r['actor_id']}> (`{r['event_count']}/{r['threshold']}`) → **{r['action_taken']}** (`{r['created_at'][:10]}`)"
            for r in rows
        ]
        await ctx.reply(
            embed=make_embed(
                title=f"🚨 Security Incident Log — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.DANGER,
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(AntiNukeCog(bot))
