"""
Cosmic ki baddie — Dynamic Role & Command Permission Engine
Evaluates command execution rights, hierarchy boundaries, Safe Mode restrictions,
channel allow/deny lists, and configurable role permissions stored in SQLite/PostgreSQL.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands

from config import CONFIG

if TYPE_CHECKING:
    from main import CosmicBot

# Default logical permission tiers mapped to guild role configurations when no explicit override exists
CATEGORY_DEFAULT_TIER: Dict[str, str] = {
    "permissions": "admin",
    "commandmanager": "admin",
    "antinuke": "admin",
    "protection": "admin",
    "setupwizard": "admin",
    "roles": "admin",
    "welcome": "admin",
    "embeds": "mod",
    "moderation": "mod",
    "logging": "admin",
    "automation": "admin",
    "autoresponder": "mod",
    "tickets": "member",
    "giveaways": "event",
    "events": "event",
    "polls": "member",
    "help": "member",
}

# Commands considered destructive/sensitive that are restricted when Safe Mode is active
SAFE_MODE_RESTRICTED_COMMANDS: set[str] = {
    "ban",
    "kick",
    "purge",
    "role create",
    "role delete",
    "role edit",
    "role give",
    "role remove",
    "autoresponder create",
    "autoresponder delete",
    "giveaway create",
}


class PermissionEngine:
    """
    Central authority for verifying whether a member can execute a command
    or target another member in a moderation action.
    """

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    @staticmethod
    def is_emergency_admin(member: discord.Member) -> bool:
        """
        Emergency management route:
        Guild Owner, Discord Administrator permission, or configured Bot Owner IDs
        ALWAYS retain emergency access so a server can never lock itself out.
        """
        if member.id in CONFIG.owner_ids:
            return True
        if member.guild.owner_id == member.id:
            return True
        if member.guild_permissions.administrator:
            return True
        return False

    async def evaluate_command_access(
        self,
        guild: discord.Guild,
        member: discord.Member,
        channel: discord.abc.GuildChannel,
        command_name: str,
        cog_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Evaluate whether `member` is allowed to invoke `command_name` in `channel`.
        Returns `(allowed: bool, reason_if_denied: str)`.
        """
        cmd_key = command_name.lower().strip()
        root_key = cmd_key.split(" ")[0]
        cat_key = (cog_name or "").lower().replace("_", "")

        guild_cfg = await self.bot.db.ensure_guild(guild.id, CONFIG.default_prefix)
        cmd_setting = await self.bot.db.get_command_setting(guild.id, cmd_key)
        root_setting = (
            await self.bot.db.get_command_setting(guild.id, root_key)
            if root_key != cmd_key
            else cmd_setting
        )

        is_admin = self.is_emergency_admin(member)

        # 1. Safe Mode check
        if guild_cfg.get("safe_mode") and not is_admin:
            if cmd_key in SAFE_MODE_RESTRICTED_COMMANDS or root_key in SAFE_MODE_RESTRICTED_COMMANDS:
                return (
                    False,
                    "🛡️ **Safe Mode** is currently active. Sensitive management and destructive commands are restricted to Server Administrators.",
                )

        # 2. Enabled / Disabled check (Protected commands can never be disabled)
        if cmd_key not in CONFIG.protected_commands and root_key not in CONFIG.protected_commands:
            if not cmd_setting["enabled"] or not root_setting["enabled"]:
                return False, f"🚫 The command `{cmd_key}` is currently disabled on this server."
            if not cmd_setting["category_enabled"] or not root_setting["category_enabled"]:
                return False, f"🚫 The `{cog_name or 'selected'}` command category is currently disabled."

        # Emergency administrators bypass channel/role restrictions
        if is_admin:
            return True, "Authorized via emergency administrator privilege."

        # 3. Channel allow/deny checks
        channel_id = getattr(channel, "id", 0)
        denied_channels = set(cmd_setting["denied_channels"]) | set(root_setting["denied_channels"])
        if channel_id in denied_channels:
            return False, f"🚫 `{cmd_key}` cannot be used in <#{channel_id}>."

        allowed_channels = set(cmd_setting["allowed_channels"]) | set(root_setting["allowed_channels"])
        if allowed_channels and channel_id not in allowed_channels:
            formatted = ", ".join(f"<#{cid}>" for cid in list(allowed_channels)[:5])
            return False, f"🚫 `{cmd_key}` can only be used in: {formatted}."

        member_role_ids = {r.id for r in getattr(member, "roles", [])}

        # 4. Command-level explicit role deny/allow from `command_settings`
        denied_roles = set(cmd_setting["denied_roles"]) | set(root_setting["denied_roles"])
        if member_role_ids & denied_roles:
            return False, "🚫 One of your roles is explicitly denied access to this command."

        cmd_allowed_roles = set(cmd_setting["allowed_roles"]) | set(root_setting["allowed_roles"])

        # 5. Granular rules in `role_permissions` table (supports command_name, root_key, category:*, *)
        all_rules = await self.bot.db.get_permission_rules(guild.id)
        candidate_targets = {cmd_key, root_key, f"category:{cat_key}", "*"}
        relevant_rules = [r for r in all_rules if r["command_name"] in candidate_targets]

        # Check explicit user deny / allow first
        for rule in relevant_rules:
            if rule["target_type"] == "user" and rule["target_id"] == member.id:
                if rule["effect"] == "deny":
                    return False, "🚫 You have an explicit deny override for this command."
                if rule["effect"] == "allow":
                    return True, "Authorized via user allow rule."

        # Check explicit role deny
        for rule in relevant_rules:
            if rule["target_type"] == "role" and rule["target_id"] in member_role_ids:
                if rule["effect"] == "deny":
                    return False, f"🚫 Role <@&{rule['target_id']}> is denied access to `{cmd_key}`."

        # Check explicit role allow (either in role_permissions or command_settings)
        if member_role_ids & cmd_allowed_roles:
            return True, "Authorized via command allowed roles."

        has_any_allow_rule_for_cmd = bool(cmd_allowed_roles)
        for rule in relevant_rules:
            if rule["effect"] == "allow":
                has_any_allow_rule_for_cmd = True
                if rule["target_type"] == "role" and rule["target_id"] in member_role_ids:
                    return True, f"Authorized via role <@&{rule['target_id']}>."

        # If explicit allow rules were configured for this command and user matched none, deny
        if has_any_allow_rule_for_cmd:
            return False, "🚫 You do not have any of the authorized roles required for this command."

        # 6. Fallback to Server Role Tiers configured in `guild_configs`
        admin_role_id = guild_cfg.get("admin_role_id")
        mod_role_id = guild_cfg.get("mod_role_id")
        support_role_id = guild_cfg.get("support_role_id")
        event_role_id = guild_cfg.get("event_manager_role_id")
        member_role_id = guild_cfg.get("member_role_id")

        # Public member commands within staff cogs
        public_subcommands = {
            "userinfo",
            "serverinfo",
            "help",
            "prefix view",
            "giveaway list",
            "events list",
            "poll vote",
            "rank",
            "leaderboard",
            "ticket create",
            "ticket close",
        }
        if cmd_key in public_subcommands or root_key in {"help", "userinfo", "serverinfo", "rank", "leaderboard"}:
            return True, "Public member command."

        tier = CATEGORY_DEFAULT_TIER.get(cat_key, "member")
        if tier == "admin":
            if admin_role_id and admin_role_id in member_role_ids:
                return True, "Authorized via configured Admin role."
            return False, "🚫 This command requires the configured **Admin Role** or Discord **Administrator** permission."

        if tier == "mod":
            allowed_tier_roles = {r for r in (admin_role_id, mod_role_id) if r}
            if member_role_ids & allowed_tier_roles:
                return True, "Authorized via configured Moderator/Admin role."
            if (
                member.guild_permissions.moderate_members
                or member.guild_permissions.kick_members
                or member.guild_permissions.ban_members
                or member.guild_permissions.manage_messages
            ):
                return True, "Authorized via Discord moderation permissions."
            return False, "🚫 This command requires the configured **Moderator Role** or Discord Moderation permissions."

        if tier == "support":
            allowed_tier_roles = {r for r in (admin_role_id, mod_role_id, support_role_id) if r}
            if member_role_ids & allowed_tier_roles or member.guild_permissions.manage_channels:
                return True, "Authorized via Support/Moderator role."
            return False, "🚫 This command requires the configured **Support Role** or **Moderator Role**."

        if tier == "event":
            allowed_tier_roles = {r for r in (admin_role_id, mod_role_id, event_role_id) if r}
            if member_role_ids & allowed_tier_roles or member.guild_permissions.manage_events:
                return True, "Authorized via Event Manager/Moderator role."
            return False, "🚫 This command requires the configured **Event Manager Role** or **Moderator Role**."

        if member_role_id and member_role_id not in member_role_ids:
            # Only enforce member_role_id if explicitly configured and user has no staff roles
            staff_roles = {r for r in (admin_role_id, mod_role_id, support_role_id, event_role_id) if r}
            if not (member_role_ids & staff_roles):
                return False, f"🚫 You need the <@&{member_role_id}> role to use member commands."

        return True, "Authorized."

    async def check_mod_action_permission(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        action: str,
    ) -> Tuple[bool, str]:
        """
        Verify if a moderator's role is permitted to execute a specific punishment action
        (e.g. `ban`, `kick`, `timeout`, `warn`, `mute`, `purge`) based on `mod_role_permissions`.
        """
        if self.is_emergency_admin(moderator):
            return True, "Authorized as administrator."

        rows = await self.bot.db.fetchall(
            "SELECT * FROM mod_role_permissions WHERE guild_id = ?", (guild.id,)
        )
        if not rows:
            # Default: fall back to standard command evaluation
            return True, "Default moderation access."

        mod_role_ids = {r.id for r in moderator.roles}
        allowed_actions: set[str] = set()
        matched_any_role = False
        for row in rows:
            if row["role_id"] in mod_role_ids:
                matched_any_role = True
                actions = json.loads(row.get("allowed_actions_json") or "[]")
                allowed_actions.update(a.lower() for a in actions)

        if matched_any_role and action.lower() not in allowed_actions and "*" not in allowed_actions:
            return (
                False,
                f"🚫 Your moderator role tier is not authorized to perform the `{action}` punishment action.",
            )
        return True, "Authorized for punishment action."

    @staticmethod
    def check_hierarchy(
        guild: discord.Guild,
        actor: discord.Member,
        target: discord.Member,
        bot_member: Optional[discord.Member] = None,
    ) -> Tuple[bool, str]:
        """
        Enforce Discord and Cosmic ki baddie role hierarchy:
        - Cannot target oneself.
        - Cannot target the Server Owner.
        - Non-owners cannot target members with an equal or higher top role.
        - Bot cannot target members with an equal or higher top role than the bot.
        """
        if actor.id == target.id:
            return False, "🚫 You cannot perform this moderation action on yourself."
        if target.id == guild.owner_id:
            return False, "🚫 You cannot perform moderation actions on the Server Owner."
        if actor.id != guild.owner_id and actor.top_role <= target.top_role:
            return (
                False,
                f"🚫 Hierarchy protection: **{target}**'s highest role ({target.top_role.mention}) is equal to or higher than yours ({actor.top_role.mention}).",
            )
        if bot_member is not None and bot_member.top_role <= target.top_role:
            return (
                False,
                f"🚫 Discord hierarchy restriction: **{target}**'s highest role ({target.top_role.mention}) is equal to or higher than my highest role ({bot_member.top_role.mention}).",
            )
        return True, "Hierarchy check passed."
