"""
Cosmic ki baddie — Main Bot Entrypoint & Core Orchestrator
Server: Side Quest Studio
Optimized for Bot-Hosting.net / Bot-Hosting.com (Pterodactyl), Railway, Render, and VPS.
Implements automatic dependency bootstrapping on Bot-Hosting containers, dynamic multi-prefix
resolution, hybrid command synchronization, global permission & cooldown enforcement,
custom alias routing, persistent views, and automatic SQLite backup scheduling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, List, Optional


def ensure_bothosting_requirements() -> None:
    """
    On Bot-Hosting.net / Pterodactyl containers, automatically install `requirements.txt`
    if `discord.py` is not yet installed in the container environment.
    """
    if "discord" in sys.modules:
        return
    try:
        import discord  # noqa: F401
    except ImportError:
        req_file = Path(__file__).resolve().parent / "requirements.txt"
        if req_file.exists() and os.getenv("SKIP_AUTO_PIP", "").lower() not in ("1", "true"):
            print("[Cosmic ki baddie] Installing Python dependencies from requirements.txt for Bot-Hosting...")
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "-r", str(req_file)]
                )
            except Exception as exc:
                print(f"[Cosmic ki baddie] Automatic pip install warning: {exc}")


if __name__ == "__main__":
    ensure_bothosting_requirements()

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
from discord.ext import commands, tasks  # noqa: E402

from config import CONFIG, THEME  # noqa: E402
from database.database import DatabaseManager  # noqa: E402
from utils.checks import COOLDOWN_MANAGER  # noqa: E402
from utils.embeds import error_embed, info_embed, make_embed, success_embed  # noqa: E402
from utils.permissions import PermissionEngine  # noqa: E402


def setup_logging() -> logging.Logger:
    """Configure structured console and file logging without ever exposing secrets."""
    log_dir = Path(__file__).resolve().parent / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, CONFIG.log_level, logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_dir / "cosmic_bot.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger("CosmicBot")
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return root_logger


logger = setup_logging()


class CosmicTree(app_commands.CommandTree):
    """
    Custom CommandTree that enforces Cosmic ki baddie's dynamic command settings,
    Safe Mode, channel rules, cooldowns, and role permissions on EVERY slash command.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot: "CosmicBot" = interaction.client  # type: ignore
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return True

        command = interaction.command
        if command is None:
            return True

        qualified_name = command.qualified_name.lower().strip()
        cog_name = getattr(command.binding, "__cog_name__", None)

        allowed, reason = await bot.permissions_engine.evaluate_command_access(
            guild=interaction.guild,
            member=interaction.user,
            channel=interaction.channel,  # type: ignore
            command_name=qualified_name,
            cog_name=cog_name,
        )
        if not allowed:
            embed = error_embed("Access Denied", reason)
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False

        # Dynamic cooldown check (bypassed by emergency admins)
        if not bot.permissions_engine.is_emergency_admin(interaction.user):
            setting = await bot.db.get_command_setting(interaction.guild.id, qualified_name)
            cooldown_sec = setting["cooldown_seconds"]
            if cooldown_sec <= 0 and " " in qualified_name:
                root_setting = await bot.db.get_command_setting(
                    interaction.guild.id, qualified_name.split(" ")[0]
                )
                cooldown_sec = root_setting["cooldown_seconds"]
            rem = COOLDOWN_MANAGER.check_and_trigger(
                interaction.guild.id, qualified_name, interaction.user.id, cooldown_sec
            )
            if rem > 0:
                embed = error_embed(
                    "Command Cooldown",
                    f"⏳ Please wait **{rem}s** before using `/{qualified_name}` again.",
                )
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return False

        return True


class CosmicBot(commands.Bot):
    """
    Production Discord management bot for Side Quest Studio ("Cosmic ki baddie").
    """

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        intents = discord.Intents.all()
        super().__init__(
            command_prefix=self._dynamic_prefix_resolver,
            intents=intents,
            help_command=None,  # Managed by our interactive `cogs/help.py`
            tree_cls=CosmicTree,
            case_insensitive=True,
            owner_ids=set(CONFIG.owner_ids) if CONFIG.owner_ids else None,
        )
        self.db: DatabaseManager = db or DatabaseManager(
            CONFIG.database_url, backup_dir=CONFIG.backup_dir
        )
        self.permissions_engine = PermissionEngine(self)
        self.snipe_cache: dict[int, dict[str, Any]] = {}
        self.editsnipe_cache: dict[int, dict[str, Any]] = {}
        self.startup_time = discord.utils.utcnow()
        self.add_check(self._global_prefix_command_check)

    async def _dynamic_prefix_resolver(
        self, bot: commands.Bot, message: discord.Message
    ) -> List[str]:
        """Resolve per-guild custom prefixes (plus bot mention) from the persistent database."""
        if message.guild is None:
            return commands.when_mentioned_or(CONFIG.default_prefix)(bot, message)
        prefixes = await self.db.get_prefixes(message.guild.id, CONFIG.default_prefix)
        return commands.when_mentioned_or(*prefixes)(bot, message)

    async def _global_prefix_command_check(self, ctx: commands.Context[Any]) -> bool:
        """Global gatekeeper for all prefix and hybrid commands."""
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            return True
        if ctx.command is None:
            return True

        qualified_name = ctx.command.qualified_name.lower().strip()
        cog_name = ctx.command.cog_name

        allowed, reason = await self.permissions_engine.evaluate_command_access(
            guild=ctx.guild,
            member=ctx.author,
            channel=ctx.channel,  # type: ignore
            command_name=qualified_name,
            cog_name=cog_name,
        )
        if not allowed:
            await ctx.reply(embed=error_embed("Access Denied", reason), mention_author=False)
            return False

        if not self.permissions_engine.is_emergency_admin(ctx.author):
            setting = await self.db.get_command_setting(ctx.guild.id, qualified_name)
            cooldown_sec = setting["cooldown_seconds"]
            if cooldown_sec <= 0 and " " in qualified_name:
                root_setting = await self.db.get_command_setting(
                    ctx.guild.id, qualified_name.split(" ")[0]
                )
                cooldown_sec = root_setting["cooldown_seconds"]
            rem = COOLDOWN_MANAGER.check_and_trigger(
                ctx.guild.id, qualified_name, ctx.author.id, cooldown_sec
            )
            if rem > 0:
                await ctx.reply(
                    embed=error_embed(
                        "Command Cooldown",
                        f"⏳ Please wait **{rem}s** before using `{qualified_name}` again.",
                    ),
                    mention_author=False,
                )
                return False

        return True

    async def setup_hook(self) -> None:
        """Initialize database, register core prefix commands, load all cogs, and restore persistent views."""
        await self.db.initialize()
        self._register_core_prefix_commands()

        for ext in CONFIG.initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info("Loaded extension: %s", ext)
            except Exception as exc:
                logger.error("Failed to load extension %s: %s", ext, exc)
                traceback.print_exc()

        if CONFIG.sync_commands_on_startup and self.user is not None:
            try:
                if CONFIG.dev_guild_id:
                    guild_obj = discord.Object(id=CONFIG.dev_guild_id)
                    self.tree.copy_global_to(guild=guild_obj)
                    synced = await self.tree.sync(guild=guild_obj)
                    logger.info("Synced %d slash commands to dev guild %s", len(synced), CONFIG.dev_guild_id)
                else:
                    synced = await self.tree.sync()
                    logger.info("Synced %d global slash commands", len(synced))
            except Exception as exc:
                logger.warning("Slash command sync skipped or failed: %s", exc)

        if not self.daily_backup_task.is_running():
            self.daily_backup_task.start()

    def _register_core_prefix_commands(self) -> None:
        """Register the `/prefix` hybrid command group directly on the bot."""

        @commands.hybrid_group(
            name="prefix",
            description="View or customize command prefixes for Side Quest Studio.",
            invoke_without_command=True,
        )
        async def prefix_group(ctx: commands.Context[Any]) -> None:
            if ctx.guild is None:
                await ctx.reply(
                    embed=info_embed("Current Prefix", f"Default prefix: `{CONFIG.default_prefix}`")
                )
                return
            prefixes = await self.db.get_prefixes(ctx.guild.id, CONFIG.default_prefix)
            formatted = ", ".join(f"`{p}`" for p in prefixes)
            embed = info_embed(
                "Server Command Prefixes",
                f"Active prefixes for **{ctx.guild.name}**: {formatted}\n"
                f"Slash commands (`/`) are always active regardless of prefix.",
            )
            await ctx.reply(embed=embed, mention_author=False)

        @prefix_group.command(name="view", description="Display all active prefixes for this server.")
        async def prefix_view(ctx: commands.Context[Any]) -> None:
            await prefix_group.callback(ctx)  # type: ignore

        @prefix_group.command(
            name="set",
            description="Set the primary command prefix (or comma-separated multiple prefixes).",
        )
        @app_commands.describe(new_prefix="New prefix (or comma-separated prefixes, e.g. !, ?, cs!)")
        async def prefix_set(ctx: commands.Context[Any], *, new_prefix: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                return
            guild_cfg = await self.db.ensure_guild(ctx.guild.id)
            admin_role_id = guild_cfg.get("admin_role_id")
            member_role_ids = {r.id for r in ctx.author.roles}
            if not (
                self.permissions_engine.is_emergency_admin(ctx.author)
                or (admin_role_id and admin_role_id in member_role_ids)
                or ctx.author.guild_permissions.manage_guild
            ):
                await ctx.reply(
                    embed=error_embed(
                        "Permission Denied",
                        "You need the **Admin Role** or **Manage Server** permission to change prefixes.",
                    ),
                    ephemeral=True,
                )
                return

            parts = [p.strip() for p in new_prefix.split(",") if p.strip()]
            if not parts or any(len(p) > 10 for p in parts):
                await ctx.reply(
                    embed=error_embed(
                        "Invalid Prefix",
                        "Please provide 1–5 prefixes, each up to 10 characters long.",
                    ),
                    ephemeral=True,
                )
                return

            updated = await self.db.set_prefixes(ctx.guild.id, parts)
            formatted = ", ".join(f"`{p}`" for p in updated)
            await ctx.reply(
                embed=success_embed(
                    "Prefix Updated",
                    f"Active prefixes for **{ctx.guild.name}** are now: {formatted}",
                )
            )
            logging_cog = self.get_cog("Logging")
            if logging_cog and hasattr(logging_cog, "log_config_change"):
                await logging_cog.log_config_change(
                    ctx.guild,
                    ctx.author,
                    "Prefix Updated",
                    f"New prefixes: {formatted}",
                )

        @prefix_group.command(name="add", description="Add an additional prefix to this server.")
        @app_commands.describe(prefix="Additional prefix to add")
        async def prefix_add(ctx: commands.Context[Any], prefix: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                return
            if not (
                self.permissions_engine.is_emergency_admin(ctx.author)
                or ctx.author.guild_permissions.manage_guild
            ):
                await ctx.reply(
                    embed=error_embed("Permission Denied", "Manage Server permission required."),
                    ephemeral=True,
                )
                return
            current = await self.db.get_prefixes(ctx.guild.id, CONFIG.default_prefix)
            cleaned = prefix.strip()
            if cleaned not in current:
                current.append(cleaned)
            updated = await self.db.set_prefixes(ctx.guild.id, current)
            await ctx.reply(
                embed=success_embed(
                    "Prefix Added",
                    f"Active prefixes: {', '.join(f'`{p}`' for p in updated)}",
                )
            )

        @prefix_group.command(name="remove", description="Remove a prefix from this server.")
        @app_commands.describe(prefix="Prefix to remove")
        async def prefix_remove(ctx: commands.Context[Any], prefix: str) -> None:
            if ctx.guild is None or not isinstance(ctx.author, discord.Member):
                return
            if not (
                self.permissions_engine.is_emergency_admin(ctx.author)
                or ctx.author.guild_permissions.manage_guild
            ):
                await ctx.reply(
                    embed=error_embed("Permission Denied", "Manage Server permission required."),
                    ephemeral=True,
                )
                return
            current = await self.db.get_prefixes(ctx.guild.id, CONFIG.default_prefix)
            cleaned = prefix.strip()
            remaining = [p for p in current if p != cleaned] or [CONFIG.default_prefix]
            updated = await self.db.set_prefixes(ctx.guild.id, remaining)
            await ctx.reply(
                embed=success_embed(
                    "Prefix Removed",
                    f"Active prefixes: {', '.join(f'`{p}`' for p in updated)}",
                )
            )

        self.add_command(prefix_group)

    @tasks.loop(hours=24)
    async def daily_backup_task(self) -> None:
        """Create an automatic daily SQLite backup."""
        try:
            await self.db.create_backup(max_keep=CONFIG.max_Backup_retention)
        except Exception as exc:
            logger.error("Automatic database backup failed: %s", exc)

    async def on_ready(self) -> None:
        """Set presence and log readiness when connected to Discord."""
        if self.user:
            logger.info(
                "%s (%s) is online and connected to %d guild(s)!",
                CONFIG.bot_name,
                self.user,
                len(self.guilds),
            )
        activity = discord.Activity(
            type=discord.ActivityType.watching,
            name=CONFIG.status_text,
        )
        await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_message(self, message: discord.Message) -> None:
        """
        Process prefix commands and support dynamic custom aliases configured via `/command settings`.
        """
        if message.author.bot:
            return
        if message.guild is not None:
            prefixes = await self.db.get_prefixes(message.guild.id, CONFIG.default_prefix)
            matched_prefix = next((p for p in prefixes if message.content.startswith(p)), None)
            if matched_prefix:
                raw_body = message.content[len(matched_prefix) :].strip()
                if raw_body:
                    first_token = raw_body.split(" ")[0].lower()
                    if self.get_command(first_token) is None:
                        rows = await self.db.fetchall(
                            "SELECT command_name, aliases_json FROM command_settings WHERE guild_id = ?",
                            (message.guild.id,),
                        )
                        for row in rows:
                            aliases = [a.lower() for a in json.loads(row.get("aliases_json") or "[]")]
                            if first_token in aliases:
                                rest = raw_body[len(first_token) :]
                                message.content = f"{matched_prefix}{row['command_name']}{rest}"
                                break
        await self.process_commands(message)

    async def on_command_error(
        self, ctx: commands.Context[Any], error: commands.CommandError
    ) -> None:
        """Global error handler for prefix and hybrid commands."""
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                embed=error_embed(
                    "Missing Argument",
                    f"Missing required argument: `{error.param.name}`.\n"
                    f"Use `/help` to view command syntax and examples.",
                ),
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.reply(
                embed=error_embed("Invalid Argument", str(error)),
                mention_author=False,
            )
            return
        if isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(error.missing_permissions)
            await ctx.reply(
                embed=error_embed(
                    "Bot Missing Permissions",
                    f"I need the following Discord permission(s) to run this command: `{missing}`",
                ),
                mention_author=False,
            )
            return

        original = getattr(error, "original", error)
        logger.error("Unhandled command error in %s: %s", ctx.command, original)
        await ctx.reply(
            embed=error_embed(
                "Command Execution Error",
                f"An unexpected error occurred: `{original}`",
            ),
            mention_author=False,
        )

    async def close(self) -> None:
        """Cleanly shut down background tasks, database connections, and Discord client."""
        if self.daily_backup_task.is_running():
            self.daily_backup_task.cancel()
        await self.db.close()
        await super().close()


async def run_bot() -> None:
    """Validate environment token and start Cosmic ki baddie."""
    invalid_tokens = {
        "",
        "your_discord_bot_token_here",
        "PASTE_YOUR_DISCORD_BOT_TOKEN_HERE",
    }
    if not CONFIG.token or CONFIG.token in invalid_tokens:
        logger.critical(
            "DISCORD_TOKEN is not set! Open `bot_config.json`, `config.env`, or `.env` in your Bot-Hosting File Manager and paste your Discord Bot Token."
        )
        sys.exit(1)

    bot = CosmicBot()
    async with bot:
        await bot.start(CONFIG.token)


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user.")
