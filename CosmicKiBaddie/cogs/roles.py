"""
Cosmic ki baddie — Role Management & Persistent Role Panels (`cogs/roles.py`)
Complete role management inside Discord without editing Python code:
- Create, delete, edit, recolor, and reorder roles
- Assign temporary roles with automatic expiration loop
- Build and publish persistent self-assignable button/dropdown role panels
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
from utils.helpers import format_duration, parse_duration, parse_hex_color

if TYPE_CHECKING:
    from main import CosmicBot


class PersistentRoleButton(discord.ui.Button[Any]):
    """Persistent button that toggles a self-assignable role even after bot restarts."""

    def __init__(
        self,
        menu_name: str,
        role_id: int,
        label: str,
        emoji: Optional[str] = None,
        mode: str = "multiple",
        all_role_ids: Optional[List[int]] = None,
    ) -> None:
        custom_id = f"cosmic:rolemenu:{menu_name}:{role_id}"
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label[:80],
            emoji=emoji or "✨",
            custom_id=custom_id[:100],
        )
        self.role_id = role_id
        self.mode = mode
        self.all_role_ids = all_role_ids or []

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message(
                embed=error_embed("Role Unavailable", "This role no longer exists on the server."),
                ephemeral=True,
            )
            return

        if interaction.guild.me and role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                embed=error_embed(
                    "Hierarchy Error",
                    f"My highest role is below {role.mention}. Please ask an admin to move my role higher.",
                ),
                ephemeral=True,
            )
            return

        member = interaction.user
        if role in member.roles:
            if self.mode == "verify":
                await interaction.response.send_message(
                    embed=info_embed("Already Verified", f"You already have {role.mention}!"),
                    ephemeral=True,
                )
                return
            await member.remove_roles(role, reason="Self-assignable role panel toggle")
            await interaction.response.send_message(
                embed=info_embed("Role Removed", f"Removed {role.mention} from your profile."),
                ephemeral=True,
            )
        else:
            if self.mode == "single" and self.all_role_ids:
                to_remove = [
                    r for r in member.roles if r.id in self.all_role_ids and r.id != role.id
                ]
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Single-choice role menu switch")
            await member.add_roles(role, reason="Self-assignable role panel toggle")
            await interaction.response.send_message(
                embed=success_embed("Role Added", f"Assigned {role.mention} to your profile!"),
                ephemeral=True,
            )


class PersistentRoleMenuView(discord.ui.View):
    """Persistent View hosting self-assignable role buttons."""

    def __init__(
        self,
        menu_name: str,
        mode: str,
        roles_data: List[Dict[str, Any]],
    ) -> None:
        super().__init__(timeout=None)
        all_ids = [int(r["role_id"]) for r in roles_data]
        for item in roles_data[:25]:
            self.add_item(
                PersistentRoleButton(
                    menu_name=menu_name,
                    role_id=int(item["role_id"]),
                    label=str(item.get("label") or "Role"),
                    emoji=item.get("emoji"),
                    mode=mode,
                    all_role_ids=all_ids,
                )
            )


class RolesCog(commands.Cog, name="Roles"):
    """Server role management, temporary roles, and self-assignable role panels."""

    def __init__(self, bot: "CosmicBot") -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """Restore all persistent role menus from the database and start temp-role expiry task."""
        rows = await self.bot.db.fetchall("SELECT * FROM role_menus")
        for row in rows:
            try:
                roles_data = json.loads(row.get("roles_json") or "[]")
                if roles_data:
                    view = PersistentRoleMenuView(
                        menu_name=row["name"],
                        mode=row.get("mode", "multiple"),
                        roles_data=roles_data,
                    )
                    self.bot.add_view(view)
            except Exception:
                continue
        if not self.temp_role_expiry_loop.is_running():
            self.temp_role_expiry_loop.start()

    def cog_unload(self) -> None:
        if self.temp_role_expiry_loop.is_running():
            self.temp_role_expiry_loop.cancel()

    @tasks.loop(seconds=30)
    async def temp_role_expiry_loop(self) -> None:
        """Check and remove expired temporary roles (including timed mutes)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        expired_rows = await self.bot.db.fetchall(
            "SELECT * FROM temp_roles WHERE active = 1 AND expires_at <= ?", (now_iso,)
        )
        for row in expired_rows:
            await self.bot.db.execute(
                "UPDATE temp_roles SET active = 0 WHERE id = ?", (row["id"],)
            )
            guild = self.bot.get_guild(int(row["guild_id"]))
            if guild is None:
                continue
            member = guild.get_member(int(row["user_id"]))
            role = guild.get_role(int(row["role_id"]))
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Temporary role duration expired")
                except Exception:
                    continue

    # =========================================================================
    # /roles Command Group
    # =========================================================================
    @commands.hybrid_group(
        name="roles",
        description="Create, edit, delete, recolor, and manage server roles and role panels.",
        invoke_without_command=True,
    )
    async def roles_group(self, ctx: commands.Context[Any]) -> None:
        if ctx.guild is None:
            return
        roles = [r for r in reversed(ctx.guild.roles) if r.id != ctx.guild.default_role.id]
        lines = [f"• {r.mention} (`{r.id}`) — `{len(r.members)}` members" for r in roles[:25]]
        await ctx.reply(
            embed=make_embed(
                title=f"🎭 Server Roles — {ctx.guild.name} ({len(roles)})",
                description="\n".join(lines) or "No custom roles.",
                color=THEME.PRIMARY,
            )
        )

    @roles_group.command(name="create", description="Create a new role on the server.")
    @app_commands.describe(
        name="Name of the new role",
        hex_color="Hex color (e.g. #8B5CF6)",
        hoist="Display role members separately in the member list",
        mentionable="Allow anyone to @mention this role",
    )
    async def roles_create(
        self,
        ctx: commands.Context[Any],
        name: str,
        hex_color: str = "#8B5CF6",
        hoist: bool = False,
        mentionable: bool = False,
    ) -> None:
        if ctx.guild is None:
            return
        color_val = parse_hex_color(hex_color, THEME.PRIMARY)
        role = await ctx.guild.create_role(
            name=name[:100],
            color=discord.Color(color_val),
            hoist=hoist,
            mentionable=mentionable,
            reason=f"Role created by {ctx.author}",
        )
        await ctx.reply(
            embed=success_embed(
                "Role Created",
                f"Created role {role.mention} (`{role.id}`) with color `#{color_val:06X}`.",
            )
        )

    @roles_group.command(name="delete", description="Delete an existing server role.")
    @app_commands.describe(role="Role to delete")
    async def roles_delete(
        self, ctx: commands.Context[Any], role: discord.Role
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            await ctx.reply(
                embed=error_embed("Hierarchy Protection", "You cannot delete a role equal to or higher than your own."),
                ephemeral=True,
            )
            return
        role_name = role.name
        await role.delete(reason=f"Role deleted by {ctx.author}")
        await ctx.reply(
            embed=success_embed("Role Deleted", f"Deleted role **`@{role_name}`**.")
        )

    @roles_group.command(
        name="edit", description="Edit a role's name, color, hoist, or mentionable state."
    )
    @app_commands.describe(
        role="Role to edit",
        new_name="Optional new role name",
        hex_color="Optional new hex color (e.g. #EC4899)",
        hoist="Optional: hoist role in sidebar",
        mentionable="Optional: make role mentionable",
    )
    async def roles_edit(
        self,
        ctx: commands.Context[Any],
        role: discord.Role,
        new_name: Optional[str] = None,
        hex_color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            await ctx.reply(
                embed=error_embed("Hierarchy Protection", "You cannot edit a role equal to or higher than your own."),
                ephemeral=True,
            )
            return
        kwargs: Dict[str, Any] = {"reason": f"Role edited by {ctx.author}"}
        if new_name:
            kwargs["name"] = new_name[:100]
        if hex_color:
            kwargs["color"] = discord.Color(parse_hex_color(hex_color, role.color.value))
        if hoist is not None:
            kwargs["hoist"] = hoist
        if mentionable is not None:
            kwargs["mentionable"] = mentionable
        await role.edit(**kwargs)
        await ctx.reply(
            embed=success_embed("Role Updated", f"Updated settings for {role.mention}.")
        )

    @roles_group.command(name="color", description="Change a role's hex color.")
    @app_commands.describe(role="Target role", hex_color="Hex color (e.g. #06B6D4)")
    async def roles_color(
        self, ctx: commands.Context[Any], role: discord.Role, hex_color: str
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
            await ctx.reply(
                embed=error_embed("Hierarchy Protection", "You cannot modify a role equal to or higher than your own."),
                ephemeral=True,
            )
            return
        val = parse_hex_color(hex_color, THEME.PRIMARY)
        await role.edit(color=discord.Color(val), reason=f"Role color changed by {ctx.author}")
        await ctx.reply(
            embed=success_embed(
                "Role Color Updated",
                f"Changed color of {role.mention} to `#{val:06X}`.",
            )
        )

    @roles_group.command(
        name="position",
        description="Change a role's hierarchy position where Discord permissions allow.",
    )
    @app_commands.describe(role="Role to move", position="New numeric position (1 = bottom)")
    async def roles_position(
        self, ctx: commands.Context[Any], role: discord.Role, position: int
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        if ctx.guild.me and position >= ctx.guild.me.top_role.position:
            await ctx.reply(
                embed=error_embed(
                    "Hierarchy Restriction",
                    f"Position must be lower than my highest role position (`{ctx.guild.me.top_role.position}`).",
                ),
                ephemeral=True,
            )
            return
        await role.edit(position=max(1, position), reason=f"Position updated by {ctx.author}")
        await ctx.reply(
            embed=success_embed(
                "Role Position Updated",
                f"Moved {role.mention} to position `{role.position}`.",
            )
        )

    @roles_group.command(
        name="temp",
        description="Assign a temporary role to a member that automatically expires.",
    )
    @app_commands.describe(
        member="Member to receive the temporary role",
        role="Role to assign temporarily",
        duration="Duration (e.g. 30m, 12h, 7d)",
    )
    async def roles_temp(
        self,
        ctx: commands.Context[Any],
        member: discord.Member,
        role: discord.Role,
        duration: str,
    ) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return
        seconds = parse_duration(duration)
        if not seconds or seconds < 30:
            await ctx.reply(
                embed=error_embed("Invalid Duration", "Minimum temporary role duration is `30s` (e.g. `1h`, `3d`)."),
                ephemeral=True,
            )
            return
        if ctx.guild.me and role >= ctx.guild.me.top_role:
            await ctx.reply(
                embed=error_embed("Hierarchy Restriction", f"My highest role is below {role.mention}."),
                ephemeral=True,
            )
            return

        await member.add_roles(role, reason=f"Temporary role ({duration}) by {ctx.author}")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
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
            (ctx.guild.id, member.id, role.id, ctx.author.id, expires_at.isoformat()),
        )
        await ctx.reply(
            embed=success_embed(
                "Temporary Role Assigned",
                f"Assigned {role.mention} to {member.mention} for **{format_duration(seconds)}** "
                f"(expires {discord.utils.format_dt(expires_at, 'R')}).",
            )
        )

    # =========================================================================
    # Self-Assignable Role Menu Commands
    # =========================================================================
    @roles_group.command(
        name="menu_create",
        description="Create a self-assignable button role menu.",
    )
    @app_commands.describe(
        name="Unique short identifier for this menu (e.g. 'pings', 'colors')",
        mode="Selection mode: multiple, single, or verify",
        title="Embed title for the role panel",
        description="Embed description explaining the roles",
    )
    async def roles_menu_create(
        self,
        ctx: commands.Context[Any],
        name: str,
        mode: str = "multiple",
        title: str = "✨ Self-Assignable Roles",
        *,
        description: str = "Click the buttons below to toggle your community roles!",
    ) -> None:
        if ctx.guild is None:
            return
        m = mode.lower().strip()
        if m not in ("multiple", "single", "verify"):
            await ctx.reply(
                embed=error_embed("Invalid Mode", "Mode must be `multiple`, `single`, or `verify`."),
                ephemeral=True,
            )
            return
        clean_name = name.lower().strip().replace(" ", "_")[:32]
        await self.bot.db.execute(
            """
            INSERT OR REPLACE INTO role_menus (guild_id, name, title, description, mode, roles_json, created_at)
            VALUES (?, ?, ?, ?, ?, '[]', ?)
            """
            if not self.bot.db.is_postgres
            else """
            INSERT INTO role_menus (guild_id, name, title, description, mode, roles_json, created_at)
            VALUES (?, ?, ?, ?, ?, '[]', ?)
            ON CONFLICT (guild_id, name) DO UPDATE SET title = EXCLUDED.title, description = EXCLUDED.description, mode = EXCLUDED.mode
            """,
            (ctx.guild.id, clean_name, title, description, m, datetime.now(timezone.utc).isoformat()),
        )
        await ctx.reply(
            embed=success_embed(
                "Role Menu Created",
                f"Created role menu **`{clean_name}`** (`{m}` mode).\n"
                f"Next, add roles with `/roles menu_add_role {clean_name} <@Role> <Label> [Emoji]` and publish with `/roles menu_publish {clean_name} <#channel>`.",
            )
        )

    @roles_group.command(
        name="menu_add_role",
        description="Add a role button to an existing role menu.",
    )
    @app_commands.describe(
        menu_name="Name of the role menu",
        role="Role to toggle when clicked",
        label="Button label text",
        emoji="Optional button emoji (e.g. 🎮)",
    )
    async def roles_menu_add_role(
        self,
        ctx: commands.Context[Any],
        menu_name: str,
        role: discord.Role,
        label: str,
        emoji: str = "✨",
    ) -> None:
        if ctx.guild is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM role_menus WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, menu_name.lower().strip()),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Menu Not Found", f"No role menu named `{menu_name}`."),
                ephemeral=True,
            )
            return
        roles_data: List[Dict[str, Any]] = json.loads(row.get("roles_json") or "[]")
        roles_data = [r for r in roles_data if int(r["role_id"]) != role.id]
        roles_data.append(
            {"role_id": role.id, "label": label[:80], "emoji": emoji.strip() or "✨"}
        )
        await self.bot.db.execute(
            "UPDATE role_menus SET roles_json = ? WHERE id = ?",
            (json.dumps(roles_data), row["id"]),
        )
        await ctx.reply(
            embed=success_embed(
                "Role Added to Menu",
                f"Added {role.mention} (`{label}`) to menu **`{row['name']}`** ({len(roles_data)} roles total).",
            )
        )

    @roles_group.command(
        name="menu_publish",
        description="Publish a configured self-assignable role panel to a channel.",
    )
    @app_commands.describe(menu_name="Role menu name", channel="Target text channel")
    async def roles_menu_publish(
        self,
        ctx: commands.Context[Any],
        menu_name: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if ctx.guild is None:
            return
        target_ch = channel or (ctx.channel if isinstance(ctx.channel, discord.TextChannel) else None)
        if target_ch is None:
            return
        row = await self.bot.db.fetchone(
            "SELECT * FROM role_menus WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, menu_name.lower().strip()),
        )
        if not row:
            await ctx.reply(
                embed=error_embed("Menu Not Found", f"No role menu named `{menu_name}`."),
                ephemeral=True,
            )
            return
        roles_data: List[Dict[str, Any]] = json.loads(row.get("roles_json") or "[]")
        if not roles_data:
            await ctx.reply(
                embed=error_embed(
                    "Menu Empty",
                    f"Add at least one role using `/roles menu_add_role {row['name']}` before publishing.",
                ),
                ephemeral=True,
            )
            return

        role_lines = [
            f"{r.get('emoji', '✨')} <@&{r['role_id']}> — **{r['label']}**"
            for r in roles_data
        ]
        embed = make_embed(
            title=row["title"],
            description=f"{row['description']}\n\n" + "\n".join(role_lines),
            color=THEME.PRIMARY,
        )
        view = PersistentRoleMenuView(row["name"], row["mode"], roles_data)
        self.bot.add_view(view)
        msg = await target_ch.send(embed=embed, view=view)
        await self.bot.db.execute(
            "UPDATE role_menus SET channel_id = ?, message_id = ? WHERE id = ?",
            (target_ch.id, msg.id, row["id"]),
        )
        await ctx.reply(
            embed=success_embed(
                "Role Panel Published",
                f"Published role menu **`{row['name']}`** to {target_ch.mention}!",
            )
        )


async def setup(bot: "CosmicBot") -> None:
    await bot.add_cog(RolesCog(bot))
