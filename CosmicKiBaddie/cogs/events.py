"""
Cosmic ki baddie — Server Event Management System (`cogs/events.py`)
Features:
- Create community events with date/time, location, and rich announcement embeds
- Persistent RSVP buttons (Going ✅, Interested ⭐, Can't Go ❌)
- Automatic reminder loop before events begin
- Event cancellation and event history
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed
from utils.helpers import parse_duration

if TYPE_CHECKING:
    from main import CosmicBot


class PersistentEventRSVPView(discord.ui.View):
    """Persistent RSVP buttons for Side Quest Studio community events."""

    def __init__(self, cog: "EventsCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Going",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="cosmic:event:going",
    )
    async def going_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_rsvp(interaction, "going")

    @discord.ui.button(
        label="Interested",
        style=discord.ButtonStyle.primary,
        emoji="⭐",
        custom_id="cosmic:event:interested",
    )
    async def interested_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_rsvp(interaction, "interested")

    @discord.ui.button(
        label="Can't Make It",
        style=discord.ButtonStyle.secondary,
        emoji="❌",
        custom_id="cosmic:event:declined",
    )
    async def declined_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_rsvp(interaction, "declined")


class EventsCog(commands.Cog, name="Events"):
    """Community event scheduler, persistent RSVP manager, and automated reminder service."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(PersistentEventRSVPView(self))
        if not self.event_reminder_loop.is_running():
            self.event_reminder_loop.start()

    def cog_unload(self) -> None:
        if self.event_reminder_loop.is_running():
            self.event_reminder_loop.cancel()

    def build_event_embed(self, row: dict[str, Any]) -> discord.Embed:
        going: List[int] = json.loads(row.get("going_users_json") or "[]")
        interested: List[int] = json.loads(row.get("interested_users_json") or "[]")
        declined: List[int] = json.loads(row.get("declined_users_json") or "[]")

        try:
            starts_dt = datetime.fromisoformat(row["starts_at"])
            time_str = f"{discord.utils.format_dt(starts_dt, 'F')} ({discord.utils.format_dt(starts_dt, 'R')})"
        except Exception:
            time_str = str(row["starts_at"])

        embed = make_embed(
            title=f"📅 Event #{row['id']} — {row['title']}",
            description=row["description"],
            color=THEME.ACCENT if row["status"] == "scheduled" else THEME.NEUTRAL,
        )
        embed.add_field(name="🕒 Starts At", value=time_str, inline=False)
        embed.add_field(name="📍 Location", value=str(row.get("location") or "Side Quest Stage"), inline=True)
        embed.add_field(name="👑 Organizer", value=f"<@{row['creator_id']}>", inline=True)
        embed.add_field(name="📌 Status", value=f"`{row['status'].upper()}`", inline=True)
        embed.add_field(
            name=f"✅ Going ({len(going)})",
            value=", ".join(f"<@{u}>" for u in going[:15]) or "*None yet*",
            inline=True,
        )
        embed.add_field(
            name=f"⭐ Interested ({len(interested)})",
            value=", ".join(f"<@{u}>" for u in interested[:15]) or "*None yet*",
            inline=True,
        )
        embed.add_field(
            name=f"❌ Declined ({len(declined)})",
            value=f"`{len(declined)}` member(s)",
            inline=True,
        )
        return embed

    async def handle_rsvp(self, interaction: discord.Interaction, choice: str) -> None:
        if interaction.guild is None or interaction.message is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM community_events WHERE message_id = ?",
            (interaction.message.id,),
        )
        if not row or row["status"] in ("cancelled", "completed"):
            await interaction.response.send_message(
                embed=error_embed("Event Closed", "RSVPs for this event are closed."),
                ephemeral=True,
            )
            return

        uid = interaction.user.id
        going: List[int] = [u for u in json.loads(row.get("going_users_json") or "[]") if u != uid]
        interested: List[int] = [u for u in json.loads(row.get("interested_users_json") or "[]") if u != uid]
        declined: List[int] = [u for u in json.loads(row.get("declined_users_json") or "[]") if u != uid]

        if choice == "going":
            going.append(uid)
        elif choice == "interested":
            interested.append(uid)
        else:
            declined.append(uid)

        await self.bot.db.execute(
            """
            UPDATE community_events
            SET going_users_json = ?, interested_users_json = ?, declined_users_json = ?
            WHERE id = ?
            """,
            (json.dumps(going), json.dumps(interested), json.dumps(declined), row["id"]),
        )
        updated_row = await self.bot.db.fetchone(
            "SELECT * FROM community_events WHERE id = ?", (row["id"],)
        )
        if updated_row:
            await interaction.response.edit_message(
                embed=self.build_event_embed(updated_row),
                view=PersistentEventRSVPView(self),
            )

    @tasks.loop(seconds=30)
    async def event_reminder_loop(self) -> None:
        """Send automatic reminders 15 minutes before scheduled events and mark started events."""
        now = datetime.now(timezone.utc)
        rows = await self.bot.db.fetchall(
            "SELECT * FROM community_events WHERE status = 'scheduled'"
        )
        for row in rows:
            try:
                starts_dt = datetime.fromisoformat(row["starts_at"])
            except Exception:
                continue

            diff_sec = (starts_dt - now).total_seconds()
            if diff_sec <= 900 and not row.get("reminder_sent"):
                await self.bot.db.execute(
                    "UPDATE community_events SET reminder_sent = 1 WHERE id = ?",
                    (row["id"],),
                )
                guild = self.bot.get_guild(int(row["guild_id"]))
                channel = guild.get_channel(int(row["channel_id"])) if guild else None
                if isinstance(channel, discord.TextChannel):
                    going = json.loads(row.get("going_users_json") or "[]")
                    interested = json.loads(row.get("interested_users_json") or "[]")
                    pings = " ".join(f"<@{u}>" for u in (going + interested)[:40])
                    try:
                        await channel.send(
                            content=pings or None,
                            embed=info_embed(
                                f"⏰ Event Starting Soon — {row['title']}",
                                f"**{row['title']}** starts {discord.utils.format_dt(starts_dt, 'R')} at **{row.get('location')}**!",
                            ),
                        )
                    except Exception:
                        pass

            if diff_sec <= 0:
                await self.bot.db.execute(
                    "UPDATE community_events SET status = 'completed' WHERE id = ?",
                    (row["id"],),
                )

    # =========================================================================
    # /events Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="events",
        description="Create and manage Side Quest Studio community events and RSVPs.",
        invoke_without_command=True,
    )
    async def events_group(self, ctx: commands.Context[Any]) -> None:
        await self.events_list(ctx)

    @events_group.command(
        name="create",
        description="Create a new community event with RSVP buttons and automatic reminders.",
    )
    @app_commands.describe(
        title="Event title",
        starts_in="When the event starts (e.g. 2h, 1d, 30m or ISO date YYYY-MM-DD HH:MM)",
        location="Voice channel or stage location name",
        channel="Channel to publish the event announcement in",
        description="Event description and details",
    )
    async def events_create(
        self,
        ctx: commands.Context[Any],
        title: str,
        starts_in: str,
        location: str = "Side Quest Stage",
        channel: Optional[discord.TextChannel] = None,
        *,
        description: str = "Join us for a Side Quest Studio community event!",
    ) -> None:
        if ctx.guild is None:
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return

        now = datetime.now(timezone.utc)
        dur_sec = parse_duration(starts_in)
        if dur_sec:
            starts_dt = now + timedelta(seconds=dur_sec)
        else:
            try:
                parsed = datetime.fromisoformat(starts_in.strip())
                starts_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                await ctx.reply(
                    embed=error_embed(
                        "Invalid Time Format",
                        "Provide a relative time like `2h`, `1d3h` or an ISO timestamp like `2026-11-15 18:00`.",
                    ),
                    ephemeral=True,
                )
                return

        ev_id = await self.bot.db.execute(
            """
            INSERT INTO community_events (
                guild_id, channel_id, creator_id, title, description,
                starts_at, location, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
            """,
            (
                ctx.guild.id,
                target_ch.id,
                ctx.author.id,
                title[:150],
                description,
                starts_dt.isoformat(),
                location[:100],
                now.isoformat(),
            ),
        )
        row = await self.bot.db.fetchone(
            "SELECT * FROM community_events WHERE id = ? OR (guild_id = ? AND title = ? ORDER BY id DESC LIMIT 1)",
            (ev_id, ctx.guild.id, title[:150]),
        )
        if not row:
            rows = await self.bot.db.fetchall(
                "SELECT * FROM community_events WHERE guild_id = ? ORDER BY id DESC LIMIT 1",
                (ctx.guild.id,),
            )
            row = rows[0]

        msg = await target_ch.send(
            embed=self.build_event_embed(row),
            view=PersistentEventRSVPView(self),
        )
        await self.bot.db.execute(
            "UPDATE community_events SET message_id = ? WHERE id = ?",
            (msg.id, row["id"]),
        )
        await ctx.reply(
            embed=success_embed(
                "Event Published",
                f"Event **`#{row['id']}` — {title}** is now live in {target_ch.mention}!",
            ),
            ephemeral=True,
        )

    @events_group.command(
        name="cancel", description="Cancel a scheduled community event."
    )
    @app_commands.describe(event_id="Event ID to cancel")
    async def events_cancel(self, ctx: commands.Context[Any], event_id: int) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM community_events WHERE guild_id = ? AND id = ?",
            (ctx.guild.id, event_id),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Event Not Found", f"No event with ID `#{event_id}`."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE community_events SET status = 'cancelled' WHERE id = ?", (event_id,)
        )
        await ctx.reply(
            embed=warning_embed(
                "Event Cancelled",
                f"Cancelled event **`#{event_id}` — {row['title']}**.",
            )
        )

    @events_group.command(
        name="list", description="List upcoming scheduled events."
    )
    async def events_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM community_events WHERE guild_id = ? AND status = 'scheduled' ORDER BY starts_at ASC",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed("Upcoming Events", "No upcoming events scheduled. Use `/events create` to host one!")
            )
            return
        lines = []
        for r in rows:
            going = json.loads(r.get("going_users_json") or "[]")
            lines.append(
                f"• **`#{r['id']}` {r['title']}** — 📍 {r['location']} • `{len(going)}` Going • <#{r['channel_id']}>"
            )
        await ctx.reply(
            embed=make_embed(
                title=f"📅 Upcoming Events — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.ACCENT,
            )
        )

    @events_group.command(
        name="history", description="View past and cancelled events in this server."
    )
    async def events_history(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM community_events WHERE guild_id = ? ORDER BY id DESC LIMIT 15",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(embed=info_embed("Event History", "No events recorded yet."))
            return
        lines = [
            f"• **`#{r['id']}` {r['title']}** [`{r['status'].upper()}`] — Created `{r['created_at'][:10]}`"
            for r in rows
        ]
        await ctx.reply(
            embed=make_embed(
                title=f"📜 Event History — {ctx.guild.name}",
                description="\n".join(lines),
                color=THEME.PRIMARY,
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(EventsCog(bot))
