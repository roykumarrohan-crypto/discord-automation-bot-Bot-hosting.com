"""
Cosmic ki baddie — Giveaway Management System (`cogs/giveaways.py`)
Commands:
/giveaway create
/giveaway end
/giveaway reroll
/giveaway list
/giveaway cancel
Supports persistent entry buttons, role restrictions, minimum account age,
minimum server membership tenure, automatic winner announcements, and giveaway logs.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed
from utils.helpers import format_duration, parse_duration

if TYPE_CHECKING:
    from main import CosmicBot


class PersistentGiveawayView(discord.ui.View):
    """Persistent button view allowing members to enter active giveaways across bot restarts."""

    def __init__(self, cog: "GiveawaysCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Enter Giveaway",
        style=discord.ButtonStyle.primary,
        emoji="🎉",
        custom_id="cosmic:giveaway:enter",
    )
    async def enter_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_entry(interaction)


class GiveawaysCog(commands.Cog, name="Giveaways"):
    """Full giveaway system with eligibility validation and automatic winner drawing."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(PersistentGiveawayView(self))
        if not self.giveaway_check_loop.is_running():
            self.giveaway_check_loop.start()

    def cog_unload(self) -> None:
        if self.giveaway_check_loop.is_running():
            self.giveaway_check_loop.cancel()

    @tasks.loop(seconds=15)
    async def giveaway_check_loop(self) -> None:
        """Automatically finalize giveaways whose end time has passed."""
        now_iso = datetime.now(timezone.utc).isoformat()
        due = await self.bot.db.fetchall(
            "SELECT * FROM giveaways WHERE status = 'active' AND ends_at <= ?",
            (now_iso,),
        )
        for row in due:
            await self.finalize_giveaway(row)

    async def handle_entry(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or interaction.message is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE message_id = ? AND status = 'active'",
            (interaction.message.id,),
        )
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Giveaway Ended", "This giveaway is no longer active."),
                ephemeral=True,
            )
            return

        member = interaction.user
        now = datetime.now(timezone.utc)

        # 1. Required Role check
        if row.get("required_role_id"):
            req_role = interaction.guild.get_role(int(row["required_role_id"]))
            if req_role and req_role not in member.roles:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Entry Requirement Not Met",
                        f"You must have the {req_role.mention} role to enter this giveaway.",
                    ),
                    ephemeral=True,
                )
                return

        # 2. Minimum Account Age check
        min_acc_days = int(row.get("min_account_age_days") or 0)
        if min_acc_days > 0 and member.created_at:
            acc_age = (now - member.created_at).days
            if acc_age < min_acc_days:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Account Too New",
                        f"Your Discord account must be at least **{min_acc_days} days** old to enter (current: `{acc_age}d`).",
                    ),
                    ephemeral=True,
                )
                return

        # 3. Minimum Server Tenure check
        min_srv_days = int(row.get("min_server_days") or 0)
        if min_srv_days > 0 and member.joined_at:
            srv_age = (now - member.joined_at).days
            if srv_age < min_srv_days:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Membership Requirement",
                        f"You must be a member of **{interaction.guild.name}** for at least **{min_srv_days} days** (current: `{srv_age}d`).",
                    ),
                    ephemeral=True,
                )
                return

        entries: List[int] = json.loads(row.get("entries_json") or "[]")
        if member.id in entries:
            entries.remove(member.id)
            action_msg = "You have left the giveaway."
        else:
            entries.append(member.id)
            action_msg = f"🎉 You are now entered to win **{row['prize']}**! (Total entries: `{len(entries)}`)"

        await self.bot.db.execute(
            "UPDATE giveaways SET entries_json = ? WHERE id = ?",
            (json.dumps(entries), row["id"]),
        )
        await interaction.response.send_message(
            embed=info_embed("Giveaway Entry", action_msg), ephemeral=True
        )

    async def finalize_giveaway(
        self, row: dict[str, Any], reroll: bool = False
    ) -> List[int]:
        """Draw winners for a giveaway, update database, and announce in the channel."""
        entries: List[int] = json.loads(row.get("entries_json") or "[]")
        winner_count = max(1, int(row.get("winner_count") or 1))
        winners = random.sample(entries, min(len(entries), winner_count)) if entries else []

        await self.bot.db.execute(
            "UPDATE giveaways SET status = 'ended', winners_json = ? WHERE id = ?",
            (json.dumps(winners), row["id"]),
        )

        guild = self.bot.get_guild(int(row["guild_id"]))
        if guild is None:
            return winners
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return winners

        winner_mentions = ", ".join(f"<@{w}>" for w in winners) if winners else "*No valid entries*"
        embed = make_embed(
            title=f"🎊 Giveaway {'Rerolled' if reroll else 'Ended'} — {row['prize']}",
            description=(
                f"**Prize:** {row['prize']}\n"
                f"**Winner(s):** {winner_mentions}\n"
                f"**Total Entries:** `{len(entries)}`\n"
                f"**Hosted By:** <@{row['host_id']}>"
            ),
            color=THEME.SUCCESS if winners else THEME.WARNING,
        )
        try:
            await channel.send(
                content=f"🎉 Congratulations {winner_mentions}!" if winners else None,
                embed=embed,
            )
        except Exception:
            pass
        return winners

    # =========================================================================
    # /giveaway Commands
    # =========================================================================
    @commands.hybrid_group(
        name="giveaway",
        description="Create and manage community giveaways with entry requirements.",
        invoke_without_command=True,
    )
    async def giveaway_group(self, ctx: commands.Context[Any]) -> None:
        await self.giveaway_list(ctx)

    @giveaway_group.command(
        name="create",
        description="Start a new interactive giveaway with optional role and age requirements.",
    )
    @app_commands.describe(
        duration="Duration (e.g. 30m, 12h, 3d)",
        winners="Number of winners (default 1)",
        prize="Prize title/description",
        channel="Channel to host the giveaway in (defaults to current channel)",
        required_role="Optional role required to enter",
        min_account_days="Optional minimum Discord account age in days",
        min_server_days="Optional minimum days in this server",
    )
    async def giveaway_create(
        self,
        ctx: commands.Context[Any],
        duration: str,
        winners: int,
        prize: str,
        channel: Optional[discord.TextChannel] = None,
        required_role: Optional[discord.Role] = None,
        min_account_days: int = 0,
        min_server_days: int = 0,
    ) -> None:
        if ctx.guild is None:
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return

        seconds = parse_duration(duration)
        if not seconds or seconds < 15:
            await ctx.reply(
                embed=error_embed("Invalid Duration", "Minimum giveaway duration is `15s` (e.g. `1h`, `1d`)."),
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(seconds=seconds)
        w_count = max(1, min(50, winners))

        reqs = []
        if required_role:
            reqs.append(f"• Required Role: {required_role.mention}")
        if min_account_days > 0:
            reqs.append(f"• Min Account Age: `{min_account_days} days`")
        if min_server_days > 0:
            reqs.append(f"• Min Server Tenure: `{min_server_days} days`")

        embed = make_embed(
            title=f"🎉 GIVEAWAY — {prize}",
            description=(
                f"Click **🎉 Enter Giveaway** below to participate!\n\n"
                f"**Winners:** `{w_count}`\n"
                f"**Ends:** {discord.utils.format_dt(ends_at, 'R')} ({discord.utils.format_dt(ends_at, 'f')})\n"
                f"**Hosted By:** {ctx.author.mention}"
            ),
            color=THEME.SECONDARY,
        )
        if reqs:
            embed.add_field(name="📋 Entry Requirements", value="\n".join(reqs), inline=False)

        msg = await target_ch.send(embed=embed, view=PersistentGiveawayView(self))
        gw_id = await self.bot.db.execute(
            """
            INSERT INTO giveaways (
                guild_id, channel_id, message_id, host_id, prize,
                winner_count, required_role_id, min_account_age_days,
                min_server_days, ends_at, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
            """,
            (
                ctx.guild.id,
                target_ch.id,
                msg.id,
                ctx.author.id,
                prize,
                w_count,
                required_role.id if required_role else None,
                max(0, min_account_days),
                max(0, min_server_days),
                ends_at.isoformat(),
                now.isoformat(),
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Giveaway Started",
                f"Giveaway `#{gw_id}` for **{prize}** is live in {target_ch.mention}!",
            ),
            ephemeral=True,
        )

    @giveaway_group.command(
        name="end", description="Immediately end an active giveaway and draw winners."
    )
    @app_commands.describe(giveaway_id="Giveaway ID (or Message ID)")
    async def giveaway_end(self, ctx: commands.Context[Any], giveaway_id: int) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND (id = ? OR message_id = ?)",
            (ctx.guild.id, giveaway_id, giveaway_id),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Not Found", f"No giveaway found with ID `{giveaway_id}`."),
                ephemeral=True,
            )
            return
        winners = await self.finalize_giveaway(row)
        await ctx.reply(
            embed=success_embed(
                "Giveaway Ended",
                f"Ended giveaway **{row['prize']}** ({len(winners)} winner(s) drawn).",
            )
        )

    @giveaway_group.command(
        name="reroll", description="Reroll winners for a completed giveaway."
    )
    @app_commands.describe(giveaway_id="Giveaway ID (or Message ID)")
    async def giveaway_reroll(self, ctx: commands.Context[Any], giveaway_id: int) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND (id = ? OR message_id = ?)",
            (ctx.guild.id, giveaway_id, giveaway_id),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Not Found", f"No giveaway found with ID `{giveaway_id}`."),
                ephemeral=True,
            )
            return
        winners = await self.finalize_giveaway(row, reroll=True)
        await ctx.reply(
            embed=success_embed(
                "Giveaway Rerolled",
                f"New winner(s) for **{row['prize']}**: "
                + (", ".join(f"<@{w}>" for w in winners) or "*No entries*"),
            )
        )

    @giveaway_group.command(
        name="cancel", description="Cancel an active giveaway without drawing winners."
    )
    @app_commands.describe(giveaway_id="Giveaway ID (or Message ID)")
    async def giveaway_cancel(self, ctx: commands.Context[Any], giveaway_id: int) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM giveaways WHERE guild_id = ? AND (id = ? OR message_id = ?)",
            (ctx.guild.id, giveaway_id, giveaway_id),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Not Found", f"No giveaway found with ID `{giveaway_id}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE giveaways SET status = 'cancelled' WHERE id = ?", (row["id"],)
        )
        await ctx.reply(
            embed=warning_embed(
                "Giveaway Cancelled",
                f"Cancelled giveaway **{row['prize']}** (`#{row['id']}`).",
            )
        )

    @giveaway_group.command(
        name="list", description="List all active and recent giveaways in this server."
    )
    async def giveaway_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 15",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed("Giveaways", "No giveaways found. Use `/giveaway create` to launch one!")
            )
            return
        lines = []
        for r in rows:
            entries = json.loads(r.get("entries_json") or "[]")
            lines.append(
                f"• **ID `#{r['id']}`** [{r['status'].upper()}] — **{r['prize']}** "
                f"(`{len(entries)}` entries • `{r['winner_count']}` winner(s)) in <#{r['channel_id']}>"
            )
        await ctx.reply(
            embed=make_embed(
                title=f"🎉 Server Giveaways — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.SECONDARY,
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(GiveawaysCog(bot))
