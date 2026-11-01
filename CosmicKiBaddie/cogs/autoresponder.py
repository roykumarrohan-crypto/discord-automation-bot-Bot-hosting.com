"""
Cosmic ki baddie — Rule-Based Autoresponder & Keyword Automation System (`cogs/autoresponder.py`)
Commands:
/autoresponder create
/autoresponder edit
/autoresponder delete
/autoresponder list
/autoresponder enable
/autoresponder disable
Supports multiple keyword triggers, exact/contains/startswith/regex matching,
case-sensitive/insensitive options, custom text & saved embed responses,
channel/role restrictions, cooldowns, automatic role assignment/removal, and workflow actions.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import embed_from_dict, error_embed, info_embed, make_embed, success_embed
from utils.helpers import render_template

if TYPE_CHECKING:
    from main import CosmicBot


class AutoresponderCog(commands.Cog, name="Autoresponder"):
    """Rule-based keyword autoresponder and automated message/role workflow engine."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot
        self._cooldowns: Dict[Tuple[int, int, int], float] = {}  # (guild_id, rule_id, user_id) -> monotonic

    @staticmethod
    def _matches_rule(
        content: str,
        triggers: List[str],
        match_type: str,
        case_sensitive: bool,
    ) -> bool:
        """Evaluate whether `content` matches any trigger in `triggers` according to `match_type`."""
        haystack = content if case_sensitive else content.lower()
        mtype = (match_type or "contains").lower()

        for raw_trig in triggers:
            if not raw_trig:
                continue
            needle = raw_trig if case_sensitive else raw_trig.lower()
            if mtype == "exact":
                if haystack.strip() == needle.strip():
                    return True
            elif mtype == "startswith":
                if haystack.startswith(needle):
                    return True
            elif mtype == "regex":
                flags = 0 if case_sensitive else re.IGNORECASE
                try:
                    if re.search(raw_trig, content, flags=flags):
                        return True
                except re.error:
                    continue
            else:  # contains
                if needle in haystack:
                    return True
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        if not message.content:
            return

        # Skip prefix commands
        prefixes = await self.bot.db.get_prefixes(message.guild.id, CONFIG.default_prefix)
        if any(message.content.startswith(p) for p in prefixes):
            return

        rules = await self.bot.db.fetchall(
            "SELECT * FROM autoresponders WHERE guild_id = ? AND enabled = 1",
            (message.guild.id,),
        )
        if not rules:
            return

        member = message.author
        member_role_ids = {r.id for r in member.roles}
        now = time.monotonic()

        for rule in rules:
            allowed_chs = set(json.loads(rule.get("allowed_channels_json") or "[]"))
            if allowed_chs and message.channel.id not in allowed_chs:
                continue

            allowed_roles = set(json.loads(rule.get("allowed_roles_json") or "[]"))
            if allowed_roles and not (member_role_ids & allowed_roles):
                continue

            ignored_roles = set(json.loads(rule.get("ignored_roles_json") or "[]"))
            if ignored_roles and (member_role_ids & ignored_roles):
                continue

            triggers = json.loads(rule.get("triggers_json") or "[]")
            if not self._matches_rule(
                message.content,
                triggers,
                rule.get("match_type", "contains"),
                bool(rule.get("case_sensitive", 0)),
            ):
                continue

            # Check per-rule cooldown
            cd = int(rule.get("cooldown_seconds") or 0)
            cd_key = (message.guild.id, int(rule["id"]), member.id)
            if cd > 0:
                last_used = self._cooldowns.get(cd_key, 0.0)
                if now - last_used < cd:
                    continue
                self._cooldowns[cd_key] = now

            # Execute automated role actions if configured
            if rule.get("assign_role_id"):
                r_add = message.guild.get_role(int(rule["assign_role_id"]))
                if r_add and r_add not in member.roles:
                    try:
                        await member.add_roles(r_add, reason=f"Autoresponder '{rule['name']}'")
                    except Exception:
                        pass

            if rule.get("remove_role_id"):
                r_rem = message.guild.get_role(int(rule["remove_role_id"]))
                if r_rem and r_rem in member.roles:
                    try:
                        await member.remove_roles(r_rem, reason=f"Autoresponder '{rule['name']}'")
                    except Exception:
                        pass

            # Execute workflow actions (e.g. reaction, DM, delete trigger)
            workflow = str(rule.get("workflow_action") or "")
            if workflow.startswith("react:"):
                emoji_str = workflow.split(":", 1)[1].strip()
                if emoji_str:
                    try:
                        await message.add_reaction(emoji_str)
                    except Exception:
                        pass

            # Build text and/or embed response
            resp_text = render_template(
                str(rule.get("response_text") or ""),
                member=member,
                guild=message.guild,
            )
            embed_obj: Optional[discord.Embed] = None
            if rule.get("embed_name"):
                saved = await self.bot.db.fetchone(
                    "SELECT embed_json FROM saved_embeds WHERE guild_id = ? AND name = ?",
                    (message.guild.id, rule["embed_name"]),
                )
                if saved:
                    embed_obj = embed_from_dict(
                        json.loads(saved["embed_json"]), member=member, guild=message.guild
                    )
            elif rule.get("embed_json"):
                try:
                    embed_obj = embed_from_dict(
                        json.loads(rule["embed_json"]), member=member, guild=message.guild
                    )
                except Exception:
                    embed_obj = None

            if resp_text or embed_obj:
                try:
                    if rule.get("reply_message", 1) and not rule.get("delete_trigger", 0):
                        await message.reply(
                            content=resp_text or None,
                            embed=embed_obj,
                            mention_author=bool(rule.get("mention_user", 1)),
                        )
                    else:
                        prefix_mention = f"{member.mention} " if rule.get("mention_user") and resp_text and "{user.mention}" not in (rule.get("response_text") or "") else ""
                        await message.channel.send(
                            content=(prefix_mention + resp_text).strip() or None,
                            embed=embed_obj,
                        )
                except Exception:
                    pass

            if rule.get("delete_trigger"):
                try:
                    await message.delete()
                except Exception:
                    pass

    # =========================================================================
    # /autoresponder Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="autoresponder",
        description="Manage keyword-triggered autoresponders and automated message workflows.",
        invoke_without_command=True,
    )
    async def autoresponder_group(self, ctx: commands.Context[Any]) -> None:
        await self.autoresponder_list(ctx)

    @autoresponder_group.command(
        name="create",
        description="Create a new rule-based keyword autoresponder.",
    )
    @app_commands.describe(
        name="Unique name for this autoresponder rule",
        triggers="Trigger keyword(s) separated by '|' (e.g. 'ip|server ip|join')",
        response="Response message text (supports {user.mention}, {server}, {member_count})",
        match_type="Match mode: contains, exact, startswith, or regex",
        case_sensitive="Whether keyword matching is case-sensitive",
        cooldown="Per-user cooldown in seconds (default 5s)",
        embed_template="Optional saved embed template name from /embed",
        assign_role="Optional role to automatically assign when triggered",
        allowed_channel="Optional channel to restrict this trigger to",
        allowed_role="Optional role required to trigger this rule",
    )
    async def autoresponder_create(
        self,
        ctx: commands.Context[Any],
        name: str,
        triggers: str,
        response: str,
        match_type: str = "contains",
        case_sensitive: bool = False,
        cooldown: int = 5,
        embed_template: Optional[str] = None,
        assign_role: Optional[discord.Role] = None,
        allowed_channel: Optional[discord.TextChannel] = None,
        allowed_role: Optional[discord.Role] = None,
    ) -> None:
        if ctx.guild is None:
            return
        mtype = match_type.lower().strip()
        if mtype not in ("contains", "exact", "startswith", "regex"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid Match Type",
                    "Match type must be `contains`, `exact`, `startswith`, or `regex`.",
                ),
                ephemeral=True,
            )
            return

        trigger_list = [t.strip() for t in triggers.split("|") if t.strip()]
        if not trigger_list:
            await ctx.reply(
                embed=error_embed("Empty Triggers", "Provide at least one trigger keyword."),
                ephemeral=True,
            )
            return

        rule_name = name.lower().strip()[:40]
        allowed_chs = [allowed_channel.id] if allowed_channel else []
        allowed_rls = [allowed_role.id] if allowed_role else []

        existing = await self.bot.db.fetchone(
            "SELECT id FROM autoresponders WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, rule_name),
        )
        if existing:
            await ctx.reply(
                embed=error_embed(
                    "Duplicate Name",
                    f"An autoresponder named `{rule_name}` already exists. Use `/autoresponder edit` to modify it.",
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.execute(
            """
            INSERT INTO autoresponders (
                guild_id, name, triggers_json, match_type, case_sensitive,
                response_text, embed_name, mention_user, reply_message,
                assign_role_id, allowed_channels_json, allowed_roles_json,
                cooldown_seconds, enabled, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                ctx.guild.id,
                rule_name,
                json.dumps(trigger_list),
                mtype,
                int(case_sensitive),
                response,
                embed_template.lower().strip() if embed_template else None,
                assign_role.id if assign_role else None,
                json.dumps(allowed_chs),
                json.dumps(allowed_rls),
                max(0, cooldown),
                ctx.author.id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await ctx.reply(
            embed=success_embed(
                f"Autoresponder Created — `{rule_name}`",
                f"**Triggers:** {', '.join(f'`{t}`' for t in trigger_list)}\n"
                f"**Match Mode:** `{mtype}` ({'Case-Sensitive' if case_sensitive else 'Case-Insensitive'})\n"
                f"**Cooldown:** `{cooldown}s`\n"
                f"**Response:** {response[:200]}",
            )
        )

    @autoresponder_group.command(
        name="edit",
        description="Edit an existing autoresponder's triggers, response, match mode, or role actions.",
    )
    @app_commands.describe(
        name="Name of the autoresponder to edit",
        triggers="Optional updated triggers separated by '|'",
        response="Optional updated response message",
        match_type="Optional match mode: contains, exact, startswith, regex",
        case_sensitive="Optional case sensitivity toggle",
        cooldown="Optional cooldown in seconds",
        assign_role="Optional role to assign when triggered",
    )
    async def autoresponder_edit(
        self,
        ctx: commands.Context[Any],
        name: str,
        triggers: Optional[str] = None,
        response: Optional[str] = None,
        match_type: Optional[str] = None,
        case_sensitive: Optional[bool] = None,
        cooldown: Optional[int] = None,
        assign_role: Optional[discord.Role] = None,
    ) -> None:
        if ctx.guild is None:
            return
        rule_name = name.lower().strip()
        row = await self.bot.db.fetchone(
            "SELECT * FROM autoresponders WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, rule_name),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Not Found", f"No autoresponder named `{rule_name}`."),
                ephemeral=True,
            )
            return

        new_triggers = (
            [t.strip() for t in triggers.split("|") if t.strip()]
            if triggers
            else json.loads(row["triggers_json"])
        )
        new_resp = response if response is not None else row["response_text"]
        new_mtype = match_type.lower().strip() if match_type else row["match_type"]
        new_case = int(case_sensitive) if case_sensitive is not None else row["case_sensitive"]
        new_cd = max(0, cooldown) if cooldown is not None else row["cooldown_seconds"]
        new_role = assign_role.id if assign_role else row.get("assign_role_id")

        await self.bot.db.execute(
            """
            UPDATE autoresponders
            SET triggers_json = ?, response_text = ?, match_type = ?,
                case_sensitive = ?, cooldown_seconds = ?, assign_role_id = ?
            WHERE id = ?
            """,
            (
                json.dumps(new_triggers),
                new_resp,
                new_mtype,
                new_case,
                new_cd,
                new_role,
                row["id"],
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Autoresponder Updated",
                f"Updated autoresponder **`{rule_name}`**.",
            )
        )

    @autoresponder_group.command(
        name="delete", description="Delete an autoresponder rule."
    )
    @app_commands.describe(name="Name of the autoresponder to delete")
    async def autoresponder_delete(self, ctx: commands.Context[Any], name: str) -> None:
        if ctx.guild is None:
            return
        rule_name = name.lower().strip()
        row = await self.bot.db.fetchone(
            "SELECT id FROM autoresponders WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, rule_name),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Not Found", f"No autoresponder named `{rule_name}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute("DELETE FROM autoresponders WHERE id = ?", (row["id"],))
        await ctx.reply(
            embed=success_embed(
                "Autoresponder Deleted", f"Deleted autoresponder **`{rule_name}`**."
            )
        )

    @autoresponder_group.command(
        name="list", description="List all configured autoresponders in this server."
    )
    async def autoresponder_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM autoresponders WHERE guild_id = ? ORDER BY name ASC",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed(
                    "Autoresponders",
                    "No autoresponders configured yet. Create one with `/autoresponder create`!",
                )
            )
            return
        lines = []
        for r in rows[:20]:
            trigs = json.loads(r.get("triggers_json") or "[]")
            status = "✅" if r.get("enabled") else "❌"
            lines.append(
                f"{status} **`{r['name']}`** [{r['match_type']}] — Triggers: {', '.join(f'`{t}`' for t in trigs[:4])} (CD: `{r['cooldown_seconds']}s`)"
            )
        await ctx.reply(
            embed=make_embed(
                title=f"🤖 Autoresponders — {ctx.guild.name} ({len(rows)})",
                description="\n".join(lines),
                color=THEME.PRIMARY,
            )
        )

    @autoresponder_group.command(
        name="enable", description="Enable a disabled autoresponder trigger."
    )
    @app_commands.describe(name="Name of the autoresponder to enable")
    async def autoresponder_enable(self, ctx: commands.Context[Any], name: str) -> None:
        if ctx.guild is None:
            return
        rule_name = name.lower().strip()
        await self.bot.db.execute(
            "UPDATE autoresponders SET enabled = 1 WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, rule_name),
        )
        await ctx.reply(
            embed=success_embed(
                "Autoresponder Enabled", f"Autoresponder **`{rule_name}`** is now active."
            )
        )

    @autoresponder_group.command(
        name="disable", description="Temporarily disable an autoresponder trigger."
    )
    @app_commands.describe(name="Name of the autoresponder to disable")
    async def autoresponder_disable(self, ctx: commands.Context[Any], name: str) -> None:
        if ctx.guild is None:
            return
        rule_name = name.lower().strip()
        await self.bot.db.execute(
            "UPDATE autoresponders SET enabled = 0 WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, rule_name),
        )
        await ctx.reply(
            embed=success_embed(
                "Autoresponder Disabled", f"Autoresponder **`{rule_name}`** is now disabled."
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(AutoresponderCog(bot))
