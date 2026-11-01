"""
Cosmic ki baddie — Interactive Embed Creation Studio (`cogs/embeds.py`)
Allows staff to build, edit, preview, save, reuse, and publish rich custom embeds
with buttons and multiple fields directly inside Discord using Modals and Views.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import CONFIG, THEME
from utils.embeds import embed_from_dict, error_embed, info_embed, make_embed, success_embed
from utils.helpers import parse_hex_color

if TYPE_CHECKING:
    from main import CosmicBot


class CoreEmbedModal(discord.ui.Modal, title="Edit Embed Core Content"):
    """Modal for editing Title, Description, Hex Color, Author, and Footer."""

    title_input = discord.ui.TextInput(
        label="Embed Title",
        required=False,
        max_length=256,
    )
    description_input = discord.ui.TextInput(
        label="Description (supports {user.mention}, {server})",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=3500,
    )
    color_input = discord.ui.TextInput(
        label="Hex Color (e.g. #8B5CF6)",
        default="#8B5CF6",
        required=False,
        max_length=12,
    )
    author_input = discord.ui.TextInput(
        label="Author Name",
        required=False,
        max_length=256,
    )
    footer_input = discord.ui.TextInput(
        label="Footer Text",
        required=False,
        max_length=256,
    )

    def __init__(self, studio_view: "EmbedStudioView") -> None:
        super().__init__()
        self.studio_view = studio_view
        d = studio_view.embed_data
        self.title_input.default = str(d.get("title") or "")
        self.description_input.default = str(d.get("description") or "")
        self.color_input.default = f"#{int(d.get('color', THEME.PRIMARY)):06X}"
        self.author_input.default = str(d.get("author_name") or "")
        self.footer_input.default = str(d.get("footer_text") or "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        d = self.studio_view.embed_data
        d["title"] = self.title_input.value
        d["description"] = self.description_input.value
        d["color"] = parse_hex_color(self.color_input.value, THEME.PRIMARY)
        d["author_name"] = self.author_input.value
        d["footer_text"] = self.footer_input.value
        await interaction.response.edit_message(
            embed=self.studio_view.build_preview(interaction),
            view=self.studio_view,
        )


class MediaEmbedModal(discord.ui.Modal, title="Edit Embed Images & URLs"):
    """Modal for setting Thumbnail URL, Main Image URL, and Author/Footer Icon URLs."""

    thumbnail_input = discord.ui.TextInput(
        label="Thumbnail Image URL (https://...)",
        required=False,
        max_length=500,
    )
    image_input = discord.ui.TextInput(
        label="Main Banner Image URL (https://...)",
        required=False,
        max_length=500,
    )
    author_icon_input = discord.ui.TextInput(
        label="Author Icon URL (https://...)",
        required=False,
        max_length=500,
    )

    def __init__(self, studio_view: "EmbedStudioView") -> None:
        super().__init__()
        self.studio_view = studio_view
        d = studio_view.embed_data
        self.thumbnail_input.default = str(d.get("thumbnail_url") or "")
        self.image_input.default = str(d.get("image_url") or "")
        self.author_icon_input.default = str(d.get("author_icon") or "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        d = self.studio_view.embed_data
        d["thumbnail_url"] = self.thumbnail_input.value.strip()
        d["image_url"] = self.image_input.value.strip()
        d["author_icon"] = self.author_icon_input.value.strip()
        await interaction.response.edit_message(
            embed=self.studio_view.build_preview(interaction),
            view=self.studio_view,
        )


class AddFieldModal(discord.ui.Modal, title="Add Embed Field"):
    """Modal for appending a field to the current embed."""

    field_name = discord.ui.TextInput(label="Field Name", required=True, max_length=256)
    field_value = discord.ui.TextInput(
        label="Field Value",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1024,
    )
    field_inline = discord.ui.TextInput(
        label="Inline? (yes / no)",
        default="yes",
        required=True,
        max_length=5,
    )

    def __init__(self, studio_view: "EmbedStudioView") -> None:
        super().__init__()
        self.studio_view = studio_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        fields = self.studio_view.embed_data.setdefault("fields", [])
        fields.append(
            {
                "name": self.field_name.value,
                "value": self.field_value.value,
                "inline": self.field_inline.value.strip().lower() in ("yes", "true", "1", "y"),
            }
        )
        await interaction.response.edit_message(
            embed=self.studio_view.build_preview(interaction),
            view=self.studio_view,
        )


class AddLinkButtonModal(discord.ui.Modal, title="Add Link Button to Embed"):
    """Modal for attaching a clickable URL button to a published embed."""

    btn_label = discord.ui.TextInput(label="Button Label", required=True, max_length=80)
    btn_url = discord.ui.TextInput(
        label="Destination URL (https://...)", required=True, max_length=500
    )
    btn_emoji = discord.ui.TextInput(
        label="Optional Emoji (e.g. 🔗)", default="🔗", required=False, max_length=10
    )

    def __init__(self, studio_view: "EmbedStudioView") -> None:
        super().__init__()
        self.studio_view = studio_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        url = self.btn_url.value.strip()
        if not url.startswith(("http://", "https://")):
            await interaction.response.send_message(
                embed=error_embed("Invalid URL", "URL must start with `https://` or `http://`."),
                ephemeral=True,
            )
            return
        self.studio_view.buttons_data.append(
            {
                "label": self.btn_label.value.strip(),
                "url": url,
                "emoji": self.btn_emoji.value.strip() or None,
            }
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Link Button Added",
                f"Added button **{self.btn_label.value}** → `{url}`.",
            ),
            ephemeral=True,
        )


class EmbedStudioView(discord.ui.View):
    """Interactive Discord studio for building, saving, and publishing custom embeds."""

    def __init__(
        self,
        bot: "CosmicBot",
        guild: discord.Guild,
        template_name: str,
        initial_data: Optional[Dict[str, Any]] = None,
        initial_buttons: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.guild = guild
        self.template_name = template_name.lower().strip()[:40]
        self.embed_data: Dict[str, Any] = initial_data or {
            "title": f"✨ {template_name.title()}",
            "description": "Click **Edit Text & Color** below to customize this embed!",
            "color": THEME.PRIMARY,
            "timestamp": True,
            "footer_text": f"{CONFIG.bot_name} • {CONFIG.community_name}",
            "fields": [],
        }
        self.buttons_data: List[Dict[str, Any]] = initial_buttons or []

    def build_preview(self, interaction: Optional[discord.Interaction] = None) -> discord.Embed:
        member = (
            interaction.user
            if interaction and isinstance(interaction.user, discord.Member)
            else None
        )
        return embed_from_dict(self.embed_data, member=member, guild=self.guild)

    @discord.ui.button(
        label="Edit Text & Color", style=discord.ButtonStyle.primary, emoji="✏️", row=0
    )
    async def edit_core_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(CoreEmbedModal(self))

    @discord.ui.button(
        label="Images & Icons", style=discord.ButtonStyle.secondary, emoji="🖼️", row=0
    )
    async def edit_media_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(MediaEmbedModal(self))

    @discord.ui.button(
        label="Add Field", style=discord.ButtonStyle.secondary, emoji="➕", row=0
    )
    async def add_field_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(AddFieldModal(self))

    @discord.ui.button(
        label="Clear Fields", style=discord.ButtonStyle.secondary, emoji="🧹", row=0
    )
    async def clear_fields_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        self.embed_data["fields"] = []
        await interaction.response.edit_message(
            embed=self.build_preview(interaction), view=self
        )

    @discord.ui.button(
        label="Toggle Timestamp", style=discord.ButtonStyle.secondary, emoji="🕒", row=1
    )
    async def toggle_ts_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        self.embed_data["timestamp"] = not bool(self.embed_data.get("timestamp", True))
        await interaction.response.edit_message(
            embed=self.build_preview(interaction), view=self
        )

    @discord.ui.button(
        label="Add Link Button", style=discord.ButtonStyle.secondary, emoji="🔗", row=1
    )
    async def add_link_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await interaction.response.send_modal(AddLinkButtonModal(self))

    @discord.ui.button(
        label="Save Template", style=discord.ButtonStyle.success, emoji="💾", row=1
    )
    async def save_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button[Any]
    ) -> None:
        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO saved_embeds (guild_id, name, embed_json, buttons_json, created_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """
            if not self.bot.db.is_postgres
            else """
            INSERT INTO saved_embeds (guild_id, name, embed_json, buttons_json, created_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, name) DO UPDATE SET embed_json = EXCLUDED.embed_json, buttons_json = EXCLUDED.buttons_json, updated_at = EXCLUDED.updated_at
            """,
            (
                self.guild.id,
                self.template_name,
                json.dumps(self.embed_data),
                json.dumps(self.buttons_data),
                interaction.user.id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await interaction.response.send_message(
            embed=success_embed(
                "Template Saved",
                f"Saved embed template **`{self.template_name}`** to database!",
            ),
            ephemeral=True,
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Publish this embed immediately to a channel...",
        min_values=1,
        max_values=1,
        row=2,
    )
    async def publish_channel_select(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect[Any]
    ) -> None:
        target = self.guild.get_channel(select.values[0].id)
        if not isinstance(target, discord.TextChannel):
            return
        pub_view = build_published_buttons_view(self.buttons_data)
        await target.send(embed=self.build_preview(interaction), view=pub_view)
        await interaction.response.send_message(
            embed=success_embed(
                "Embed Published",
                f"Published **`{self.template_name}`** to {target.mention}!",
            ),
            ephemeral=True,
        )


def build_published_buttons_view(
    buttons_data: List[Dict[str, Any]],
) -> Optional[discord.ui.View]:
    """Construct a View containing URL link buttons for a published embed."""
    if not buttons_data:
        return None
    view = discord.ui.View(timeout=None)
    for b in buttons_data[:5]:
        view.add_item(
            discord.ui.Button(
                label=str(b.get("label") or "Link")[:80],
                url=str(b["url"]),
                emoji=b.get("emoji"),
            )
        )
    return view


class EmbedsCog(commands.Cog, name="Embeds"):
    """Interactive embed builder, template manager, and channel publisher."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    @commands.hybrid_group(
        name="embed",
        description="Build, edit, preview, save, and publish custom Discord embeds interactively.",
        invoke_without_command=True,
    )
    async def embed_group(self, ctx: commands.Context[Any]) -> None:
        await self.embed_list(ctx)

    @embed_group.command(
        name="create",
        description="Launch the interactive Embed Builder Studio to create and save a new embed.",
    )
    @app_commands.describe(
        name="Template name to save under (e.g. 'rules', 'announcement')",
        title="Initial embed title",
        description="Initial embed description",
        hex_color="Initial hex color (e.g. #8B5CF6)",
    )
    async def embed_create(
        self,
        ctx: commands.Context[Any],
        name: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        hex_color: str = "#8B5CF6",
    ) -> None:
        if ctx.guild is None:
            return
        clean_name = name.lower().strip()[:40]
        data = {
            "title": title or f"✨ {name.title()}",
            "description": description or "Use the buttons below to customize this embed!",
            "color": parse_hex_color(hex_color, THEME.PRIMARY),
            "timestamp": True,
            "footer_text": f"{CONFIG.bot_name} • {CONFIG.community_name}",
            "fields": [],
        }
        # Also persist immediately so it's ready for reuse
        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO saved_embeds (guild_id, name, embed_json, buttons_json, created_by, updated_at)
            VALUES (?, ?, ?, '[]', ?, ?)
            """
            if not self.bot.db.is_postgres
            else """
            INSERT INTO saved_embeds (guild_id, name, embed_json, buttons_json, created_by, updated_at)
            VALUES (?, ?, ?, '[]', ?, ?)
            ON CONFLICT (guild_id, name) DO UPDATE SET embed_json = EXCLUDED.embed_json, updated_at = EXCLUDED.updated_at
            """,
            (
                ctx.guild.id,
                clean_name,
                json.dumps(data),
                ctx.author.id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        studio = EmbedStudioView(self.bot, ctx.guild, clean_name, initial_data=data)
        await ctx.reply(
            content=f"🎨 **Embed Studio — Template `{clean_name}`**",
            embed=studio.build_preview(ctx.interaction),
            view=studio,
        )

    @embed_group.command(
        name="edit",
        description="Open an existing saved embed template in the interactive Embed Studio.",
    )
    @app_commands.describe(name="Saved template name to edit")
    async def embed_edit(self, ctx: commands.Context[Any], name: str) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM saved_embeds WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name.lower().strip()),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Template Not Found", f"No saved embed named `{name}`."),
                ephemeral=True,
            )
            return
        studio = EmbedStudioView(
            self.bot,
            ctx.guild,
            row["name"],
            initial_data=json.loads(row["embed_json"]),
            initial_buttons=json.loads(row.get("buttons_json") or "[]"),
        )
        await ctx.reply(
            content=f"🎨 **Editing Embed Template `{row['name']}`**",
            embed=studio.build_preview(ctx.interaction),
            view=studio,
        )

    @embed_group.command(
        name="publish",
        description="Publish a saved embed template to a specified channel.",
    )
    @app_commands.describe(
        name="Saved embed template name",
        channel="Target text channel (defaults to current channel)",
    )
    async def embed_publish(
        self,
        ctx: commands.Context[Any],
        name: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if ctx.guild is None:
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM saved_embeds WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name.lower().strip()),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Template Not Found", f"No saved embed named `{name}`."),
                ephemeral=True,
            )
            return
        embed_obj = embed_from_dict(
            json.loads(row["embed_json"]),
            member=ctx.author if isinstance(ctx.author, discord.Member) else None,
            guild=ctx.guild,
        )
        btns = json.loads(row.get("buttons_json") or "[]")
        await target_ch.send(embed=embed_obj, view=build_published_buttons_view(btns))
        await ctx.reply(
            embed=success_embed(
                "Embed Published",
                f"Published embed template **`{row['name']}`** to {target_ch.mention}.",
            ),
            ephemeral=True,
        )

    @embed_group.command(
        name="delete", description="Delete a saved embed template."
    )
    @app_commands.describe(name="Name of the saved embed template to delete")
    async def embed_delete(self, ctx: commands.Context[Any], name: str) -> None:
        if ctx.guild is None:
            return
        await self.bot.db.execute(
            "DELETE FROM saved_embeds WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name.lower().strip()),
        )
        await ctx.reply(
            embed=success_embed("Template Deleted", f"Deleted embed template **`{name}`**.")
        )

    @embed_group.command(
        name="list", description="List all saved embed templates in this server."
    )
    async def embed_list(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        rows = await self.bot.db.fetchall(
            "SELECT * FROM saved_embeds WHERE guild_id = ? ORDER BY name ASC",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.reply(
                embed=info_embed(
                    "Saved Embed Templates",
                    "No saved embeds yet. Use `/embed create <name>` to launch the studio!",
                )
            )
            return
        lines = []
        for r in rows:
            d = json.loads(r["embed_json"])
            lines.append(
                f"• **`{r['name']}`** — *{d.get('title') or 'Untitled'}* (`{len(d.get('fields') or [])}` fields)"
            )
        await ctx.reply(
            embed=make_embed(
                title=f"🎨 Saved Embed Templates — {ctx.guild.name} ({len(rows)})",
                description="\n".join(lines),
                color=THEME.PRIMARY,
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(EmbedsCog(bot))
