"""
Cosmic ki baddie — Interactive Poll System (`cogs/polls.py`)
Features:
- Multiple-choice polls (up to 5 options) & Yes/No polls
- Anonymous polls vs. Public polls
- Role-restricted voting permissions
- Custom poll duration & automatic poll ending loop
- Live vote counting & visual progress-bar results embeds
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed
from utils.helpers import build_progress_bar, parse_duration

if TYPE_CHECKING:
    from main import CosmicBot

NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class PollVoteButton(discord.ui.Button[Any]):
    """Persistent button for voting on option `index` in a poll."""

    def __init__(self, cog: "PollsCog", index: int, label: str, emoji: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label[:75],
            emoji=emoji,
            custom_id=f"cosmic:poll:opt_{index}",
        )
        self.cog = cog
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_vote(interaction, self.index)


class PersistentPollView(discord.ui.View):
    """Persistent poll voting view supporting up to 5 options."""

    def __init__(self, cog: "PollsCog", options: Optional[List[str]] = None) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        opts = options or [f"Option {i+1}" for i in range(5)]
        for idx, opt_label in enumerate(opts[:5]):
            self.add_item(
                PollVoteButton(
                    cog=cog,
                    index=idx,
                    label=opt_label,
                    emoji=NUMBER_EMOJIS[idx],
                )
            )


class PollsCog(commands.Cog, name="Polls"):
    """Interactive multiple-choice and Yes/No polls with anonymous/public tallying."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(PersistentPollView(self))
        if not self.poll_end_loop.is_running():
            self.poll_end_loop.start()

    def cog_unload(self) -> None:
        if self.poll_end_loop.is_running():
            self.poll_end_loop.cancel()

    def build_poll_embed(self, row: Dict[str, Any]) -> discord.Embed:
        options: List[str] = json.loads(row.get("options_json") or "[]")
        votes: Dict[str, int] = json.loads(row.get("votes_json") or "{}")
        total_votes = len(votes)
        is_anon = bool(row.get("anonymous"))
        is_ended = row.get("status") == "ended"

        lines = []
        for idx, opt in enumerate(options):
            voters = [int(uid) for uid, choice in votes.items() if int(choice) == idx]
            bar = build_progress_bar(len(voters), total_votes, length=10)
            line = f"{NUMBER_EMOJIS[idx]} **{opt}** — `{len(voters)}` vote(s)\n{bar}"
            if not is_anon and voters:
                mentions = ", ".join(f"<@{u}>" for u in voters[:8])
                if len(voters) > 8:
                    mentions += f" (+{len(voters) - 8})"
                line += f"\n*Voters: {mentions}*"
            lines.append(line)

        meta = [
            f"🗳️ **Total Votes:** `{total_votes}`",
            f"🕵️ **Mode:** `{'Anonymous' if is_anon else 'Public'}`",
        ]
        if row.get("required_role_id"):
            meta.append(f"🎭 **Required Role:** <@&{row['required_role_id']}>")
        if row.get("ends_at") and not is_ended:
            try:
                ends_dt = datetime.fromisoformat(row["ends_at"])
                meta.append(f"⏳ **Ends:** {discord.utils.format_dt(ends_dt, 'R')}")
            except Exception:
                pass

        return make_embed(
            title=f"📊 {'[FINAL RESULTS] ' if is_ended else ''}Poll #{row['id']} — {row['question']}",
            description="\n\n".join(lines) + "\n\n" + " • ".join(meta),
            color=THEME.SUCCESS if is_ended else THEME.PRIMARY,
        )

    async def handle_vote(self, interaction: discord.Interaction, option_index: int) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or interaction.message is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM polls WHERE message_id = ?", (interaction.message.id,)
        )
        if not row or row.get("status") == "ended":
            await interaction.response.send_message(
                embed=error_embed("Poll Ended", "Voting on this poll has closed."),
                ephemeral=True,
            )
            return

        if row.get("required_role_id"):
            req_role = interaction.guild.get_role(int(row["required_role_id"]))
            if req_role and req_role not in interaction.user.roles:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Voting Restricted",
                        f"You need the {req_role.mention} role to vote in this poll.",
                    ),
                    ephemeral=True,
                )
                return

        options: List[str] = json.loads(row.get("options_json") or "[]")
        if option_index >= len(options):
            return

        votes: Dict[str, int] = json.loads(row.get("votes_json") or "{}")
        uid_key = str(interaction.user.id)
        if votes.get(uid_key) == option_index:
            votes.pop(uid_key, None)
        else:
            votes[uid_key] = option_index

        await self.bot.db.execute(
            "UPDATE polls SET votes_json = ? WHERE id = ?",
            (json.dumps(votes), row["id"]),
        )
        row["votes_json"] = json.dumps(votes)
        await interaction.response.edit_message(
            embed=self.build_poll_embed(row),
            view=PersistentPollView(self, options),
        )

    @tasks.loop(seconds=20)
    async def poll_end_loop(self) -> None:
        """Automatically close expired polls and update their embed to show final results."""
        now_iso = datetime.now(timezone.utc).isoformat()
        due = await self.bot.db.fetchall(
            "SELECT * FROM polls WHERE status = 'active' AND ends_at IS NOT NULL AND ends_at <= ?",
            (now_iso,),
        )
        for row in due:
            await self.bot.db.execute(
                "UPDATE polls SET status = 'ended' WHERE id = ?", (row["id"],)
            )
            row["status"] = "ended"
            guild = self.bot.get_guild(int(row["guild_id"]))
            channel = guild.get_channel(int(row["channel_id"])) if guild else None
            if isinstance(channel, discord.TextChannel) and row.get("message_id"):
                try:
                    msg = await channel.fetch_message(int(row["message_id"]))
                    await msg.edit(embed=self.build_poll_embed(row), view=None)
                except Exception:
                    pass

    # =========================================================================
    # /poll Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="poll",
        description="Create multiple-choice or Yes/No polls with anonymous/public voting.",
        invoke_without_command=True,
    )
    async def poll_group(self, ctx: commands.Context[Any]) -> None:
        await self.poll_list(ctx)

    @poll_group.command(
        name="create",
        description="Create a multiple-choice poll (separate 2–5 options with '|').",
    )
    @app_commands.describe(
        question="Poll question",
        options="2 to 5 options separated by '|' (e.g. 'Valorant | Minecraft | Genshin')",
        duration="Optional duration (e.g. 1h, 1d)",
        anonymous="True for anonymous voting, False for public voters",
        required_role="Optional role required to vote",
    )
    async def poll_create(
        self,
        ctx: commands.Context[Any],
        question: str,
        options: str,
        duration: Optional[str] = None,
        anonymous: bool = False,
        required_role: Optional[discord.Role] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.channel, discord.TextChannel):
            return
        opt_list = [o.strip() for o in options.split("|") if o.strip()][:5]
        if len(opt_list) < 2:
            await ctx.reply(
                embed=error_embed(
                    "Not Enough Options",
                    "Please provide 2 to 5 options separated by `|` (e.g. `Option A | Option B`).",
                ),
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        dur_sec = parse_duration(duration) if duration else None
        ends_at = (now + timedelta(seconds=dur_sec)).isoformat() if dur_sec else None

        poll_id = await self.bot.db.execute(
            """
            INSERT INTO polls (
                guild_id, channel_id, creator_id, question, poll_type,
                anonymous, required_role_id, options_json, votes_json,
                ends_at, status, created_at
            ) VALUES (?, ?, ?, ?, 'multiple', ?, ?, ?, '{}', ?, 'active', ?)
            """,
            (
                ctx.guild.id,
                ctx.channel.id,
                ctx.author.id,
                question[:250],
                int(anonymous),
                required_role.id if required_role else None,
                json.dumps(opt_list),
                ends_at,
                now.isoformat(),
            ),
        )
        rows = await self.bot.db.fetchall(
            "SELECT * FROM polls WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
            (ctx.guild.id,),
        )
        row = rows[0]
        msg = await ctx.channel.send(
            embed=self.build_poll_embed(row),
            view=PersistentPollView(self, opt_list),
        )
        await self.bot.db.execute(
            "UPDATE polls SET message_id = ? WHERE id = ?", (msg.id, row["id"])
        )
        if ctx.interaction:
            await ctx.reply(
                embed=success_embed("Poll Created", f"Poll `#{row['id']}` is live!"),
                ephemeral=True,
            )

    @poll_group.command(
        name="yesno", description="Quickly create a Yes / No community poll."
    )
    @app_commands.describe(
        question="Yes/No question",
        duration="Optional duration (e.g. 30m, 12h)",
        anonymous="Anonymous voting toggle",
    )
    async def poll_yesno(
        self,
        ctx: commands.Context[Any],
        question: str,
        duration: Optional[str] = None,
        anonymous: bool = False,
    ) -> None:
        await self.poll_create(
            ctx,
            question=question,
            options="Yes ✅ | No ❌",
            duration=duration,
            anonymous=anonymous,
        )

    @poll_group.command(
        name="end", description="End an active poll immediately and display final results."
    )
    @app_commands.describe(poll_id="Poll ID to close")
    async def poll_end(self, ctx: commands.Context[Any], poll_id: int) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM polls WHERE guild_id = ? AND id = ?",
            (ctx.guild.id, poll_id),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Poll Not Found", f"No poll with ID `#{poll_id}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE polls SET status = 'ended' WHERE id = ?", (poll_id,)
        )
        row["status"] = "ended"
        await ctx.reply(embed=self.build_poll_embed(row))

    @poll_group.command(
        name="list", description="List active and recent polls in this server."
    )
    async def poll_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM polls WHERE guild_id = ? ORDER BY id DESC LIMIT 15",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(embed=info_embed("Polls", "No polls created yet. Use `/poll create`!"))
            return
        lines = [
            f"• **`#{r['id']}`** [`{r['status'].upper()}`] — {r['question']} (`{len(json.loads(r.get('votes_json') or '{}'))}` votes)"
            for r in rows
        ]
        await ctx.reply(
            embed=make_embed(
                title=f"📊 Server Polls — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.PRIMARY,
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(PollsCog(bot))
