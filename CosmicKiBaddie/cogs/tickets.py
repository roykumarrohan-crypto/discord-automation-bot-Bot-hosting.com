"""
Cosmic ki baddie — Full-Featured Ticket Management System (`cogs/tickets.py`)
Features:
- Persistent Ticket Creation Panels with multi-category dropdowns & buttons
- Optional ticket creation modal questions
- Automatic ticket numbering & customizable ticket naming pattern
- User-specific open ticket limits
- Support role permissions & staff-only ticket controls (Claim, Close, Reopen, Delete)
- HTML & Markdown transcript archiving sent to transcript channel and ticket owner
"""

from __future__ import annotations

import html
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import error_embed, info_embed, make_embed, success_embed, warning_embed
from utils.helpers import render_template

if TYPE_CHECKING:
    from main import CosmicBot


class TicketCreationModal(discord.ui.Modal, title="Open a Support Ticket"):
    """Modal presenting custom ticket creation questions before opening the channel."""

    def __init__(
        self,
        cog: "TicketsCog",
        guild: discord.Guild,
        category_key: str,
        questions: List[str],
    ) -> None:
        super().__init__(title=f"Ticket — {category_key.title()}")
        self.cog = cog
        self.guild = guild
        self.category_key = category_key
        self.question_inputs: List[tuple[str, discord.ui.TextInput[Any]]] = []

        for q in questions[:5]:
            ti: discord.ui.TextInput[Any] = discord.ui.TextInput(
                label=q[:45],
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=500,
            )
            self.add_item(ti)
            self.question_inputs.append((q, ti))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            return
        answers = {q: ti.value for q, ti in self.question_inputs}
        await self.cog.create_ticket_channel(
            interaction=interaction,
            member=interaction.user,
            category_key=self.category_key,
            answers=answers,
        )


class PersistentTicketControlView(discord.ui.View):
    """Persistent staff & user controls inside every active ticket channel."""

    def __init__(self, cog: "TicketsCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Claim Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🙋",
        custom_id="cosmic:ticket:claim",
    )
    async def claim_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_claim_ticket(interaction)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        custom_id="cosmic:ticket:close",
    )
    async def close_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_close_ticket(interaction)

    @discord.ui.button(
        label="Reopen Ticket",
        style=discord.ButtonStyle.success,
        emoji="🔓",
        custom_id="cosmic:ticket:reopen",
    )
    async def reopen_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_reopen_ticket(interaction)

    @discord.ui.button(
        label="Transcript & Delete",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="cosmic:ticket:delete",
    )
    async def delete_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.handle_delete_ticket(interaction)


class PersistentTicketPanelView(discord.ui.View):
    """Persistent Ticket Creation Panel that survives bot restarts."""

    def __init__(self, cog: "TicketsCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="General Support",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="cosmic:ticketpanel:general",
    )
    async def general_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.prompt_or_create_ticket(interaction, "general")

    @discord.ui.button(
        label="Member Report",
        style=discord.ButtonStyle.danger,
        emoji="🛡️",
        custom_id="cosmic:ticketpanel:report",
    )
    async def report_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.prompt_or_create_ticket(interaction, "report")

    @discord.ui.button(
        label="Side Quest Inquiry",
        style=discord.ButtonStyle.success,
        emoji="🚀",
        custom_id="cosmic:ticketpanel:partnership",
    )
    async def quest_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.cog.prompt_or_create_ticket(interaction, "partnership")


class TicketsCog(commands.Cog, name="Tickets"):
    """Complete ticket management system with panels, transcripts, and staff controls."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot
        CONFIG.transcript_dir.mkdir(parents=True, exist_ok=True)

    async def cog_load(self) -> None:
        self.bot.add_view(PersistentTicketPanelView(self))
        self.bot.add_view(PersistentTicketControlView(self))

    async def _is_ticket_staff(
        self, guild: discord.Guild, member: discord.Member
    ) -> bool:
        if self.bot.permissions_engine.is_emergency_admin(member):
            return True
        if member.guild_permissions.manage_channels or member.guild_permissions.moderate_members:
            return True
        guild_cfg = await self.bot.db.ensure_guild(guild.id)
        t_cfg = await self.bot.db.ensure_ticket_config(guild.id)
        staff_roles = set(json.loads(t_cfg.get("support_roles_json") or "[]"))
        for col in ("admin_role_id", "mod_role_id", "support_role_id"):
            if guild_cfg.get(col):
                staff_roles.add(int(guild_cfg[col]))
        return bool({r.id for r in member.roles} & staff_roles)

    async def prompt_or_create_ticket(
        self, interaction: discord.Interaction, category_key: str
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        t_cfg = await self.bot.db.ensure_ticket_config(interaction.guild.id)
        if not t_cfg.get("enabled", 1):
            await interaction.response.send_message(
                embed=error_embed("Tickets Disabled", "The ticket system is currently closed."),
                ephemeral=True,
            )
            return

        questions: List[str] = json.loads(t_cfg.get("questions_json") or "[]")
        if questions:
            await interaction.response.send_modal(
                TicketCreationModal(self, interaction.guild, category_key, questions)
            )
        else:
            await self.create_ticket_channel(
                interaction, interaction.user, category_key, answers={}
            )

    async def create_ticket_channel(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        category_key: str,
        answers: Dict[str, str],
    ) -> Optional[discord.TextChannel]:
        guild = member.guild
        t_cfg = await self.bot.db.ensure_ticket_config(guild.id)

        # Enforce user open-ticket limit
        open_tickets = await self.bot.db.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? AND owner_id = ? AND status IN ('open', 'claimed')",
            (guild.id, member.id),
        )
        max_open = int(t_cfg.get("max_open_per_user") or 2)
        if len(open_tickets) >= max_open:
            embed = error_embed(
                "Ticket Limit Reached",
                f"You already have **{len(open_tickets)}** active ticket(s) open (limit: `{max_open}`).",
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return None

        ticket_num = int(t_cfg.get("next_ticket_number") or 1)
        await self.bot.db.execute(
            "UPDATE ticket_configs SET next_ticket_number = ? WHERE guild_id = ?",
            (ticket_num + 1, guild.id),
        )

        pattern = str(t_cfg.get("naming_pattern") or "ticket-{number}-{username}")
        channel_name = (
            pattern.replace("{number}", f"{ticket_num:04d}")
            .replace("{username}", member.name.lower()[:12])
            .replace("{category}", category_key.lower())
        )[:90]

        category_obj = (
            guild.get_channel(int(t_cfg["category_id"]))
            if t_cfg.get("category_id")
            else None
        )
        if not isinstance(category_obj, discord.CategoryChannel):
            category_obj = None

        overwrites: Dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        if guild.me:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
            )

        support_role_ids = set(json.loads(t_cfg.get("support_roles_json") or "[]"))
        guild_cfg = await self.bot.db.ensure_guild(guild.id)
        for col in ("admin_role_id", "mod_role_id", "support_role_id"):
            if guild_cfg.get(col):
                support_role_ids.add(int(guild_cfg[col]))

        support_mentions = []
        for rid in support_role_ids:
            role = guild.get_role(int(rid))
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )
                support_mentions.append(role.mention)

        ticket_ch = await guild.create_text_channel(
            name=channel_name,
            category=category_obj,
            overwrites=overwrites,
            topic=f"Ticket #{ticket_num:04d} | Owner: {member} ({member.id}) | Category: {category_key}",
            reason=f"Cosmic Ticket #{ticket_num:04d} opened by {member}",
        )

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.bot.db.execute(
            """
            INSERT INTO tickets (
                guild_id, ticket_number, channel_id, owner_id, category_key,
                status, answers_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                guild.id,
                ticket_num,
                ticket_ch.id,
                member.id,
                category_key,
                json.dumps(answers),
                now_iso,
            ),
        )

        welcome_txt = render_template(
            str(
                t_cfg.get("welcome_message")
                or "Welcome {user.mention}! Support staff ({support_roles}) will assist you shortly."
            ),
            member=member,
            guild=guild,
            extra={"support_roles": ", ".join(support_mentions) or "Staff"},
        )
        embed = make_embed(
            title=f"🎫 Ticket #{ticket_num:04d} — {category_key.title()}",
            description=welcome_txt,
            color=THEME.ACCENT,
        )
        embed.add_field(name="Opened By", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Category", value=f"`{category_key}`", inline=True)
        for q, ans in answers.items():
            embed.add_field(name=f"❓ {q[:240]}", value=ans[:1024] or "*No answer*", inline=False)

        await ticket_ch.send(
            content=f"{member.mention} {' '.join(support_mentions)}".strip(),
            embed=embed,
            view=PersistentTicketControlView(self),
        )

        resp_embed = success_embed(
            "Ticket Created",
            f"Your ticket has been created: {ticket_ch.mention}",
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=resp_embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=resp_embed, ephemeral=True)

        logging_cog = self.bot.get_cog("Logging")
        if logging_cog and hasattr(logging_cog, "log_ticket_event"):
            await logging_cog.log_ticket_event(
                guild,
                member,
                "Ticket Opened",
                f"Ticket `#{ticket_num:04d}` ({ticket_ch.mention}) opened in category `{category_key}`.",
            )
        return ticket_ch

    # =========================================================================
    # Ticket Lifecycle Handlers (Claim, Close, Reopen, Transcript & Delete)
    # =========================================================================
    async def handle_claim_ticket(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await self._is_ticket_staff(interaction.guild, interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Staff Only", "Only Support Staff or Moderators can claim tickets."),
                ephemeral=True,
            )
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel_id,)
        )
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Not a Ticket", "This channel is not a registered ticket."),
                ephemeral=True,
            )
            return
        await self.bot.db.execute(
            "UPDATE tickets SET status = 'claimed', claimed_by = ? WHERE id = ?",
            (interaction.user.id, row["id"]),
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Ticket Claimed",
                f"🙋 {interaction.user.mention} has claimed this ticket and will assist you!",
            )
        )

    async def handle_close_ticket(
        self, interaction: discord.Interaction, reason: str = "Resolved"
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel_id,)
        )
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Not a Ticket", "This channel is not a registered ticket."),
                ephemeral=True,
            )
            return
        is_staff = await self._is_ticket_staff(interaction.guild, interaction.user)
        if not is_staff and interaction.user.id != int(row["owner_id"]):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied", "Only the ticket owner or staff can close this ticket."),
                ephemeral=True,
            )
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.bot.db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?, close_reason = ? WHERE id = ?",
            (now_iso, interaction.user.id, reason, row["id"]),
        )
        owner = interaction.guild.get_member(int(row["owner_id"]))
        if owner and isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.set_permissions(
                    owner, view_channel=True, send_messages=False
                )
            except Exception:
                pass

        await interaction.response.send_message(
            embed=warning_embed(
                f"🔒 Ticket #{row['ticket_number']:04d} Closed",
                f"Closed by {interaction.user.mention}.\n"
                f"Staff can click **Reopen Ticket** or **Transcript & Delete**.",
            )
        )

    async def handle_reopen_ticket(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await self._is_ticket_staff(interaction.guild, interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Staff Only", "Only Support Staff can reopen closed tickets."),
                ephemeral=True,
            )
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel_id,)
        )
        if not row:
            return
        await self.bot.db.execute(
            "UPDATE tickets SET status = 'open', closed_at = NULL WHERE id = ?",
            (row["id"],),
        )
        owner = interaction.guild.get_member(int(row["owner_id"]))
        if owner and isinstance(interaction.channel, discord.TextChannel):
            try:
                await interaction.channel.set_permissions(
                    owner, view_channel=True, send_messages=True, read_message_history=True
                )
            except Exception:
                pass
        await interaction.response.send_message(
            embed=success_embed(
                "🔓 Ticket Reopened",
                f"Ticket `#{row['ticket_number']:04d}` has been reopened by {interaction.user.mention}.",
            )
        )

    async def generate_transcript(
        self, channel: discord.TextChannel, ticket_row: Dict[str, Any]
    ) -> Path:
        """Generate a rich self-contained HTML transcript file for a ticket channel."""
        messages = []
        try:
            async for m in channel.history(limit=500, oldest_first=True):
                ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                author = html.escape(str(m.author))
                body = html.escape(m.content or "")
                attachments = " ".join(
                    f'<a href="{html.escape(a.url)}">[Attachment: {html.escape(a.filename)}]</a>'
                    for a in m.attachments
                )
                messages.append(
                    f'<div class="msg"><span class="ts">[{ts}]</span> '
                    f'<strong class="author">{author}:</strong> '
                    f'<span class="body">{body} {attachments}</span></div>'
                )
        except Exception:
            pass

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ticket #{ticket_row['ticket_number']:04d} Transcript — {html.escape(channel.guild.name)}</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }}
  h1 {{ color: #a78bfa; border-bottom: 1px solid #334155; padding-bottom: 12px; }}
  .meta {{ background: #1e293b; padding: 14px; border-radius: 8px; margin-bottom: 20px; }}
  .msg {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; line-height: 1.5; }}
  .ts {{ color: #94a3b8; font-size: 0.85em; margin-right: 8px; }}
  .author {{ color: #38bdf8; margin-right: 6px; }}
  a {{ color: #f472b6; }}
</style>
</head>
<body>
  <h1>Ticket #{ticket_row['ticket_number']:04d} — {html.escape(channel.name)}</h1>
  <div class="meta">
    <p><strong>Server:</strong> {html.escape(channel.guild.name)} | <strong>Owner ID:</strong> {ticket_row['owner_id']} | <strong>Category:</strong> {html.escape(ticket_row['category_key'])}</p>
    <p><strong>Created At:</strong> {html.escape(str(ticket_row['created_at']))}</p>
  </div>
  <div class="chat">
    {"".join(messages) or "<p>No messages recorded.</p>"}
  </div>
</body>
</html>"""

        out_path = (
            CONFIG.transcript_dir
            / f"ticket_{channel.guild.id}_{ticket_row['ticket_number']:04d}.html"
        )
        out_path.write_text(html_doc, encoding="utf-8")
        return out_path

    async def handle_delete_ticket(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        if not await self._is_ticket_staff(interaction.guild, interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Staff Only", "Only Support Staff can delete tickets."),
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        row = await self.bot.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (interaction.channel.id,)
        )
        if not row:
            await interaction.response.send_message(
                embed=error_embed("Not a Ticket", "This channel is not a registered ticket."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=info_embed("Archiving Ticket", "Generating HTML transcript and deleting channel...")
        )
        transcript_path = await self.generate_transcript(interaction.channel, row)
        await self.bot.db.execute(
            "UPDATE tickets SET status = 'deleted', transcript_path = ?, closed_at = ? WHERE id = ?",
            (str(transcript_path), datetime.now(timezone.utc).isoformat(), row["id"]),
        )

        t_cfg = await self.bot.db.ensure_ticket_config(interaction.guild.id)
        log_ch_id = t_cfg.get("transcript_channel_id") or t_cfg.get("log_channel_id")
        if log_ch_id:
            log_ch = interaction.guild.get_channel(int(log_ch_id))
            if isinstance(log_ch, discord.TextChannel):
                try:
                    t_embed = make_embed(
                        title=f"📜 Ticket #{row['ticket_number']:04d} Transcript",
                        description=(
                            f"**Owner:** <@{row['owner_id']}>\n"
                            f"**Category:** `{row['category_key']}`\n"
                            f"**Deleted By:** {interaction.user.mention}"
                        ),
                        color=THEME.ACCENT,
                    )
                    await log_ch.send(
                        embed=t_embed,
                        file=discord.File(str(transcript_path), filename=transcript_path.name),
                    )
                except Exception:
                    pass

        try:
            await interaction.channel.delete(reason=f"Ticket deleted by {interaction.user}")
        except Exception:
            pass

    # =========================================================================
    # /ticket Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="ticket",
        description="Manage support tickets, ticket panels, categories, and transcripts.",
        invoke_without_command=True,
    )
    async def ticket_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_ticket_config(ctx.guild.id)
        support_roles = json.loads(cfg.get("support_roles_json") or "[]")
        embed = make_embed(
            title=f"🎫 Ticket System Configuration — {ctx.guild.name}",
            description=(
                f"**Status:** {'✅ Enabled' if cfg.get('enabled') else '❌ Disabled'}\n"
                f"**Ticket Category:** {('<#' + str(cfg['category_id']) + '>') if cfg.get('category_id') else '*Server Root*'}\n"
                f"**Transcript Channel:** {('<#' + str(cfg['transcript_channel_id']) + '>') if cfg.get('transcript_channel_id') else '*Unset*'}\n"
                f"**Support Roles:** {', '.join(f'<@&{r}>' for r in support_roles) or '*Uses Server Support/Mod Tier*'}\n"
                f"**Max Open Tickets / User:** `{cfg.get('max_open_per_user', 2)}`\n"
                f"**Naming Pattern:** `{cfg.get('naming_pattern')}`"
            ),
            color=THEME.ACCENT,
        )
        await ctx.reply(embed=embed)

    @ticket_group.command(
        name="panel",
        description="Publish an interactive persistent Ticket Creation Panel in a channel.",
    )
    @app_commands.describe(
        channel="Channel to post the ticket panel in",
        title="Custom panel embed title",
        description="Custom panel embed description",
    )
    async def ticket_panel(
        self,
        ctx: commands.Context[Any],
        channel: Optional[discord.TextChannel] = None,
        title: str = "🎫 Side Quest Studio — Support & Inquiries",
        *,
        description: str = (
            "Need help from staff, want to report an issue, or have a Side Quest collaboration inquiry?\n"
            "Click one of the category buttons below to open a private support ticket!"
        ),
    ) -> None:
        if ctx.guild is None:
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        embed = make_embed(title=title, description=description, color=THEME.ACCENT)
        await target_ch.send(embed=embed, view=PersistentTicketPanelView(self))
        await ctx.reply(
            embed=success_embed(
                "Ticket Panel Published",
                f"Published persistent ticket creation panel in {target_ch.mention}.",
            ),
            ephemeral=True,
        )

    @ticket_group.command(
        name="setup",
        description="Configure ticket category, transcript log channel, support role, and per-user limit.",
    )
    @app_commands.describe(
        category="Discord CategoryChannel where open tickets will be created",
        transcript_channel="Channel where closed ticket HTML transcripts are archived",
        support_role="Support staff role granted access to all tickets",
        max_per_user="Maximum concurrent open tickets per member",
    )
    async def ticket_setup(
        self,
        ctx: commands.Context[Any],
        category: discord.CategoryChannel,
        transcript_channel: discord.TextChannel,
        support_role: discord.Role,
        max_per_user: int = 2,
    ) -> None:
        if ctx.guild is None:
            return
        cfg = await self.bot.db.ensure_ticket_config(ctx.guild.id)
        roles = list(set(json.loads(cfg.get("support_roles_json") or "[]") + [support_role.id]))
        await self.bot.db.execute(
            """
            UPDATE ticket_configs
            SET category_id = ?, transcript_channel_id = ?, log_channel_id = ?,
                support_roles_json = ?, max_open_per_user = ?
            WHERE guild_id = ?
            """,
            (
                category.id,
                transcript_channel.id,
                transcript_channel.id,
                json.dumps(roles),
                max(1, min(10, max_per_user)),
                ctx.guild.id,
            ),
        )
        await ctx.reply(
            embed=success_embed(
                "Ticket System Configured",
                f"**Category:** {category.name}\n"
                f"**Transcripts:** {transcript_channel.mention}\n"
                f"**Support Role:** {support_role.mention}\n"
                f"**Limit:** `{max_per_user}` per member",
            )
        )

    @ticket_group.command(
        name="create", description="Directly open a new support ticket."
    )
    @app_commands.describe(
        category_key="Category: general, report, or partnership",
        topic="Brief description of your request",
    )
    async def ticket_create_cmd(
        self,
        ctx: commands.Context[Any],
        category_key: str = "general",
        *,
        topic: str = "Support request",
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.interaction:
            await self.create_ticket_channel(
                ctx.interaction,
                ctx.author,
                category_key.lower().strip(),
                answers={"Topic": topic},
            )
        else:
            # Prefix invocation fallback
            t_cfg = await self.bot.db.ensure_ticket_config(ctx.guild.id)
            ticket_num = int(t_cfg.get("next_ticket_number") or 1)
            await self.bot.db.execute(
                "UPDATE ticket_configs SET next_ticket_number = ? WHERE guild_id = ?",
                (ticket_num + 1, ctx.guild.id),
            )
            ch = await ctx.guild.create_text_channel(
                name=f"ticket-{ticket_num:04d}-{ctx.author.name[:10]}",
                reason=f"Ticket created by {ctx.author}",
            )
            await self.bot.db.execute(
                """
                INSERT INTO tickets (guild_id, ticket_number, channel_id, owner_id, category_key, status, answers_json, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    ctx.guild.id,
                    ticket_num,
                    ch.id,
                    ctx.author.id,
                    category_key,
                    json.dumps({"Topic": topic}),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await ctx.reply(
                embed=success_embed("Ticket Created", f"Opened {ch.mention}!")
            )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(TicketsCog(bot))
