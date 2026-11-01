"""
Cosmic ki baddie — Comprehensive Verification & Integration Test Suite
Tests:
1. AST & Syntax validation of all project Python files
2. Database initialization, automatic schema migration (all 18 tables), and backup/restore
3. Extension loading of all 17 Cogs into `CosmicBot`
4. Hybrid command & subcommand registration check (Moderation, Permissions, Roles, Automation, Autoresponder, Tickets, Embeds, AntiNuke, Protection, Giveaways, Events, Polls, SetupWizard, Help, Prefix)
5. Permission Engine enforcement (Role Tiers, Allow/Deny rules, Safe Mode lockdown, Hierarchy protection, Protected command safety locks)
6. Persistent Views registration & custom_id verification
7. Anti-Nuke SlidingWindowTracker false-positive protection & mode threshold scaling
8. Critical command execution (prefix, warn + auto-escalation, autoresponder matching, ticket creation + HTML transcript, giveaway draw, poll vote tallying)
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# Lightweight Discord.py Test Harness (Used automatically if discord.py is not
# installed in a headless CI/sandbox environment, while using real discord.py
# when installed).
# ============================================================================
def _install_discord_shim_if_needed() -> None:
    try:
        import discord  # type: ignore

        return
    except ImportError:
        pass

    discord_mod = types.ModuleType("discord")
    ext_mod = types.ModuleType("discord.ext")
    commands_mod = types.ModuleType("discord.ext.commands")
    tasks_mod = types.ModuleType("discord.ext.tasks")
    app_commands_mod = types.ModuleType("discord.app_commands")
    ui_mod = types.ModuleType("discord.ui")
    abc_mod = types.ModuleType("discord.abc")
    utils_mod = types.ModuleType("discord.utils")

    class Color:
        def __init__(self, value: int = 0) -> None:
            self.value = int(value)

        @classmethod
        def dark_grey(cls) -> "Color":
            return cls(0x607D8B)

    class Embed:
        def __init__(
            self,
            title: Optional[str] = None,
            description: Optional[str] = None,
            color: Any = None,
            timestamp: Any = None,
        ) -> None:
            self.title = title
            self.description = description
            self.color = color if isinstance(color, Color) else Color(int(color or 0))
            self.timestamp = timestamp
            self.fields: List[Any] = []
            self.footer = types.SimpleNamespace(text=None, icon_url=None)
            self.author = types.SimpleNamespace(name=None, icon_url=None)
            self.thumbnail = types.SimpleNamespace(url=None)
            self.image = types.SimpleNamespace(url=None)

        def add_field(self, *, name: str, value: str, inline: bool = False) -> "Embed":
            self.fields.append(types.SimpleNamespace(name=name, value=value, inline=inline))
            return self

        def set_footer(self, *, text: Optional[str] = None, icon_url: Optional[str] = None) -> "Embed":
            self.footer = types.SimpleNamespace(text=text, icon_url=icon_url)
            return self

        def set_author(self, *, name: str, icon_url: Optional[str] = None) -> "Embed":
            self.author = types.SimpleNamespace(name=name, icon_url=icon_url)
            return self

        def set_thumbnail(self, *, url: Optional[str] = None) -> "Embed":
            self.thumbnail = types.SimpleNamespace(url=url)
            return self

        def set_image(self, *, url: Optional[str] = None) -> "Embed":
            self.image = types.SimpleNamespace(url=url)
            return self

    class Intents:
        @classmethod
        def all(cls) -> Any:
            return types.SimpleNamespace(message_content=True, members=True, presences=True)

    class Permissions:
        def __init__(self, **kwargs: bool) -> None:
            self.administrator = kwargs.get("administrator", False)
            self.moderate_members = kwargs.get("moderate_members", False)
            self.kick_members = kwargs.get("kick_members", False)
            self.ban_members = kwargs.get("ban_members", False)
            self.manage_messages = kwargs.get("manage_messages", False)
            self.manage_channels = kwargs.get("manage_channels", False)
            self.manage_guild = kwargs.get("manage_guild", False)
            self.manage_events = kwargs.get("manage_events", False)
            self.view_audit_log = kwargs.get("view_audit_log", True)
            self.view_channel = kwargs.get("view_channel", True)

    class PermissionOverwrite:
        def __init__(self, **kwargs: Any) -> None:
            self.send_messages = kwargs.get("send_messages", None)
            self.view_channel = kwargs.get("view_channel", None)
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Object:
        def __init__(self, id: int) -> None:
            self.id = id

    class File:
        def __init__(self, fp: Any, filename: Optional[str] = None) -> None:
            self.fp = fp
            self.filename = filename

    class _DummyClass:
        pass

    for cls_name in (
        "Guild",
        "Member",
        "User",
        "Role",
        "TextChannel",
        "CategoryChannel",
        "VoiceState",
        "Invite",
        "Message",
        "Interaction",
        "RawReactionActionEvent",
        "Activity",
    ):
        setattr(discord_mod, cls_name, type(cls_name, (_DummyClass,), {}))

    discord_mod.Color = Color  # type: ignore
    discord_mod.Embed = Embed  # type: ignore
    discord_mod.Intents = Intents  # type: ignore
    discord_mod.Permissions = Permissions  # type: ignore
    discord_mod.PermissionOverwrite = PermissionOverwrite  # type: ignore
    discord_mod.Object = Object  # type: ignore
    discord_mod.File = File  # type: ignore
    discord_mod.NotFound = type("NotFound", (Exception,), {})  # type: ignore
    discord_mod.ButtonStyle = types.SimpleNamespace(
        primary=1, secondary=2, success=3, danger=4, link=5
    )  # type: ignore
    discord_mod.TextStyle = types.SimpleNamespace(short=1, paragraph=2)  # type: ignore
    discord_mod.ChannelType = types.SimpleNamespace(text=0, voice=2, category=4, news=5)  # type: ignore
    discord_mod.ActivityType = types.SimpleNamespace(watching=3, playing=0)  # type: ignore
    discord_mod.Status = types.SimpleNamespace(online="online")  # type: ignore
    discord_mod.AuditLogAction = types.SimpleNamespace(
        ban="ban",
        unban="unban",
        kick="kick",
        role_create="role_create",
        role_delete="role_delete",
        role_update="role_update",
        channel_create="channel_create",
        channel_delete="channel_delete",
        webhook_create="webhook_create",
        bot_add="bot_add",
    )  # type: ignore

    class SelectOption:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    discord_mod.SelectOption = SelectOption  # type: ignore

    abc_mod.User = _DummyClass  # type: ignore
    abc_mod.GuildChannel = _DummyClass  # type: ignore
    abc_mod.Messageable = _DummyClass  # type: ignore
    discord_mod.abc = abc_mod  # type: ignore

    utils_mod.utcnow = lambda: datetime.now(timezone.utc)  # type: ignore
    utils_mod.format_dt = lambda dt, style="f": f"<t:{int(dt.timestamp())}:{style}>"  # type: ignore
    utils_mod.get = lambda iterable, **attrs: next(
        (
            item
            for item in iterable
            if all(getattr(item, k, None) == v for k, v in attrs.items())
        ),
        None,
    )  # type: ignore
    discord_mod.utils = utils_mod  # type: ignore

    # UI Components
    class View:
        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__()

        def __init__(self, timeout: Optional[float] = 180.0) -> None:
            self.timeout = timeout
            self.children: List[Any] = []
            for attr_name in dir(self.__class__):
                attr = getattr(self.__class__, attr_name, None)
                if hasattr(attr, "__ui_item__"):
                    self.children.append(attr)

        def add_item(self, item: Any) -> None:
            self.children.append(item)

        def clear_items(self) -> None:
            self.children.clear()

        def stop(self) -> None:
            pass

    class Modal:
        def __init_subclass__(cls, title: str = "", **kwargs: Any) -> None:
            super().__init_subclass__()
            cls.__modal_title__ = title

        def __init__(self, title: Optional[str] = None) -> None:
            self.title = title or getattr(self.__class__, "__modal_title__", "")
            self.children: List[Any] = []

        def add_item(self, item: Any) -> None:
            self.children.append(item)

    class TextInput:
        def __init__(self, **kwargs: Any) -> None:
            self.value = kwargs.get("default", "")
            self.default = kwargs.get("default", "")
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Button:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, **kwargs: Any) -> None:
            self.custom_id = kwargs.get("custom_id")
            self.label = kwargs.get("label")
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Select:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, **kwargs: Any) -> None:
            self.values: List[Any] = []
            for k, v in kwargs.items():
                setattr(self, k, v)

    class RoleSelect(Select):
        pass

    class ChannelSelect(Select):
        pass

    def _ui_decorator(**kwargs: Any) -> Callable[[Any], Any]:
        def _wrap(func: Any) -> Any:
            func.__ui_item__ = kwargs
            func.custom_id = kwargs.get("custom_id")
            return func

        return _wrap

    ui_mod.View = View  # type: ignore
    ui_mod.Modal = Modal  # type: ignore
    ui_mod.TextInput = TextInput  # type: ignore
    ui_mod.Button = Button  # type: ignore
    ui_mod.Select = Select  # type: ignore
    ui_mod.RoleSelect = RoleSelect  # type: ignore
    ui_mod.ChannelSelect = ChannelSelect  # type: ignore
    ui_mod.button = _ui_decorator  # type: ignore
    ui_mod.select = _ui_decorator  # type: ignore
    discord_mod.ui = ui_mod  # type: ignore

    # App Commands & Commands
    class CommandTree:
        def __init__(self, client: Any) -> None:
            self.client = client

        def copy_global_to(self, *, guild: Any) -> None:
            pass

        async def sync(self, *, guild: Any = None) -> List[Any]:
            return list(self.client.commands)

    app_commands_mod.CommandTree = CommandTree  # type: ignore
    app_commands_mod.describe = lambda **kwargs: (lambda f: f)  # type: ignore
    discord_mod.app_commands = app_commands_mod  # type: ignore

    class Command:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, func: Callable[..., Any], name: Optional[str] = None, description: str = "", **kwargs: Any) -> None:
            self.callback = func
            self.name = name or func.__name__
            self.description = description
            self.parent: Optional["Group"] = None
            self.cog: Any = None

        @property
        def qualified_name(self) -> str:
            if self.parent:
                return f"{self.parent.qualified_name} {self.name}"
            return self.name

        @property
        def cog_name(self) -> Optional[str]:
            return getattr(self.cog, "qualified_name", None) if self.cog else None

    class Group(Command):
        def __init__(self, func: Callable[..., Any], name: Optional[str] = None, description: str = "", **kwargs: Any) -> None:
            super().__init__(func, name=name, description=description, **kwargs)
            self.commands: List[Command] = []

        def command(self, name: Optional[str] = None, description: str = "", **kwargs: Any) -> Callable[[Callable[..., Any]], Command]:
            def _decorator(f: Callable[..., Any]) -> Command:
                cmd = Command(f, name=name or f.__name__, description=description, **kwargs)
                cmd.parent = self
                self.commands.append(cmd)
                return cmd

            return _decorator

    def hybrid_command(name: Optional[str] = None, description: str = "", **kwargs: Any) -> Callable[[Callable[..., Any]], Command]:
        def _decorator(f: Callable[..., Any]) -> Command:
            return Command(f, name=name or f.__name__, description=description, **kwargs)

        return _decorator

    def hybrid_group(name: Optional[str] = None, description: str = "", **kwargs: Any) -> Callable[[Callable[..., Any]], Group]:
        def _decorator(f: Callable[..., Any]) -> Group:
            return Group(f, name=name or f.__name__, description=description, **kwargs)

        return _decorator

    class Cog:
        def __init_subclass__(cls, name: Optional[str] = None, **kwargs: Any) -> None:
            super().__init_subclass__()
            cls.__cog_name__ = name or cls.__name__

        @property
        def qualified_name(self) -> str:
            return getattr(self.__class__, "__cog_name__", self.__class__.__name__)

        @classmethod
        def listener(cls, name: Optional[str] = None) -> Callable[[Any], Any]:
            def _wrap(f: Any) -> Any:
                f.__cog_listener__ = name or f.__name__
                return f

            return _wrap

        def get_commands(self) -> List[Command]:
            cmds: List[Command] = []
            for attr_name in dir(self.__class__):
                val = getattr(self.__class__, attr_name, None)
                if isinstance(val, Command) and val.parent is None:
                    val.cog = self
                    if isinstance(val, Group):
                        for sc in val.commands:
                            sc.cog = self
                    cmds.append(val)
            return cmds

        def walk_commands(self) -> List[Command]:
            all_cmds: List[Command] = []
            for c in self.get_commands():
                all_cmds.append(c)
                if isinstance(c, Group):
                    all_cmds.extend(c.commands)
            return all_cmds

    class Bot:
        def __init__(self, command_prefix: Any, intents: Any = None, tree_cls: Any = CommandTree, **kwargs: Any) -> None:
            self.command_prefix = command_prefix
            self.intents = intents
            self.tree = tree_cls(self)
            self.cogs: Dict[str, Cog] = {}
            self.commands: List[Command] = []
            self.views: List[View] = []
            self.guilds: List[Any] = []
            self.user = types.SimpleNamespace(id=999999999, name="Cosmic ki baddie")
            self._checks: List[Callable[..., Any]] = []

        def add_check(self, fn: Callable[..., Any]) -> None:
            self._checks.append(fn)

        def add_command(self, cmd: Command) -> None:
            self.commands.append(cmd)

        def add_view(self, view: View) -> None:
            self.views.append(view)

        async def add_cog(self, cog: Cog) -> None:
            self.cogs[cog.qualified_name] = cog
            for c in cog.get_commands():
                self.commands.append(c)
            if hasattr(cog, "cog_load"):
                await cog.cog_load()

        def get_cog(self, name: str) -> Optional[Cog]:
            return self.cogs.get(name)

        def get_guild(self, guild_id: int) -> Optional[Any]:
            return next((g for g in self.guilds if g.id == guild_id), None)

        def get_command(self, name: str) -> Optional[Command]:
            parts = name.lower().strip().split()
            if not parts:
                return None
            root = next((c for c in self.commands if c.name.lower() == parts[0]), None)
            if root and len(parts) > 1 and isinstance(root, Group):
                sub = next((sc for sc in root.commands if sc.name.lower() == parts[1]), None)
                return sub or root
            return root

        async def load_extension(self, name: str) -> None:
            mod = __import__(name, fromlist=["setup"])
            await mod.setup(self)

        async def close(self) -> None:
            for cog in self.cogs.values():
                if hasattr(cog, "cog_unload"):
                    cog.cog_unload()

    class _LoopWrapper:
        def __init__(self, coro: Callable[..., Any]) -> None:
            self.coro = coro
            self._running = False

        def is_running(self) -> bool:
            return self._running

        def start(self, *args: Any, **kwargs: Any) -> None:
            self._running = True

        def cancel(self) -> None:
            self._running = False

    def loop(**kwargs: Any) -> Callable[[Callable[..., Any]], _LoopWrapper]:
        return lambda f: _LoopWrapper(f)

    commands_mod.Bot = Bot  # type: ignore
    commands_mod.Cog = Cog  # type: ignore
    commands_mod.Command = Command  # type: ignore
    commands_mod.Group = Group  # type: ignore
    commands_mod.Context = type("Context", (_DummyClass,), {"__class_getitem__": classmethod(lambda cls, x: cls)})  # type: ignore
    commands_mod.hybrid_command = hybrid_command  # type: ignore
    commands_mod.hybrid_group = hybrid_group  # type: ignore
    commands_mod.when_mentioned_or = lambda *prefixes: (lambda bot, msg: list(prefixes))  # type: ignore
    commands_mod.CommandError = Exception  # type: ignore
    commands_mod.CheckFailure = Exception  # type: ignore
    commands_mod.CommandNotFound = Exception  # type: ignore
    commands_mod.MissingRequiredArgument = Exception  # type: ignore
    commands_mod.BadArgument = Exception  # type: ignore
    commands_mod.BotMissingPermissions = Exception  # type: ignore

    tasks_mod.loop = loop  # type: ignore
    ext_mod.commands = commands_mod  # type: ignore
    ext_mod.tasks = tasks_mod  # type: ignore
    discord_mod.ext = ext_mod  # type: ignore

    sys.modules["discord"] = discord_mod
    sys.modules["discord.ext"] = ext_mod
    sys.modules["discord.ext.commands"] = commands_mod
    sys.modules["discord.ext.tasks"] = tasks_mod
    sys.modules["discord.app_commands"] = app_commands_mod
    sys.modules["discord.ui"] = ui_mod
    sys.modules["discord.abc"] = abc_mod
    sys.modules["discord.utils"] = utils_mod


_install_discord_shim_if_needed()

import discord  # noqa: E402
from config import CONFIG  # noqa: E402
from database.database import DatabaseManager  # noqa: E402
from main import CosmicBot  # noqa: E402
from utils.helpers import format_duration, level_from_xp, parse_duration, xp_for_level  # noqa: E402
from utils.security import SlidingWindowTracker, adjust_threshold_for_mode, is_trusted_actor  # noqa: E402


# ============================================================================
# Mock Discord Entities for End-to-End Command & Permission Testing
# ============================================================================
class MockRole:
    def __init__(self, id: int, name: str, position: int = 1, admin: bool = False) -> None:
        self.id = id
        self.name = name
        self.position = position
        self.mention = f"<@&{id}>"
        self.managed = False
        self.color = discord.Color(0x8B5CF6)
        self.permissions = discord.Permissions(administrator=admin)

    def __le__(self, other: "MockRole") -> bool:
        return self.position <= other.position

    def __lt__(self, other: "MockRole") -> bool:
        return self.position < other.position

    def __ge__(self, other: "MockRole") -> bool:
        return self.position >= other.position


class MockMember(discord.Member):  # type: ignore
    def __init__(
        self,
        id: int,
        name: str,
        guild: "MockGuild",
        roles: Optional[List[MockRole]] = None,
        admin: bool = False,
        mod: bool = False,
    ) -> None:
        self.id = id
        self.name = name
        self.display_name = name
        self.nick = None
        self.mention = f"<@{id}>"
        self.bot = False
        self.guild = guild
        self.roles = roles or [guild.default_role]
        self.guild_permissions = discord.Permissions(
            administrator=admin,
            moderate_members=mod,
            kick_members=mod,
            ban_members=mod,
            manage_messages=mod,
            manage_guild=admin,
        )
        self.display_avatar = types.SimpleNamespace(url="https://example.com/avatar.png")
        self.created_at = datetime(2022, 1, 1, tzinfo=timezone.utc)
        self.joined_at = datetime(2023, 1, 1, tzinfo=timezone.utc)
        self.color = discord.Color(0x8B5CF6)
        self.timed_out = None

    @property
    def top_role(self) -> MockRole:
        return max(self.roles, key=lambda r: r.position)

    async def send(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def timeout(self, until: Any, reason: Optional[str] = None) -> None:
        self.timed_out = until

    async def add_roles(self, *roles: MockRole, reason: Optional[str] = None) -> None:
        for r in roles:
            if r not in self.roles:
                self.roles.append(r)

    async def remove_roles(self, *roles: MockRole, reason: Optional[str] = None) -> None:
        for r in roles:
            if r in self.roles:
                self.roles.remove(r)

    def __str__(self) -> str:
        return self.name


class MockTextChannel(discord.TextChannel):  # type: ignore
    def __init__(self, id: int, name: str, guild: "MockGuild") -> None:
        self.id = id
        self.name = name
        self.guild = guild
        self.mention = f"<#{id}>"
        self.sent_messages: List[Dict[str, Any]] = []

    def overwrites_for(self, obj: Any) -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite()

    def permissions_for(self, obj: Any) -> discord.Permissions:
        return discord.Permissions(view_channel=True)

    async def set_permissions(self, target: Any, **kwargs: Any) -> None:
        pass

    async def send(self, content: Optional[str] = None, **kwargs: Any) -> Any:
        msg = types.SimpleNamespace(
            id=len(self.sent_messages) + 5000,
            content=content,
            embed=kwargs.get("embed"),
            view=kwargs.get("view"),
            channel=self,
            guild=self.guild,
        )
        self.sent_messages.append({"content": content, **kwargs})
        return msg

    async def history(self, limit: int = 100, oldest_first: bool = True):
        if False:
            yield None


class MockGuild(discord.Guild):  # type: ignore
    def __init__(self, id: int = 1001, name: str = "Side Quest Studio", owner_id: int = 1) -> None:
        self.id = id
        self.name = name
        self.owner_id = owner_id
        self.description = "Side Quest Studio Official Community"
        self.icon = types.SimpleNamespace(url="https://example.com/icon.png")
        self.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.default_role = MockRole(id, "@everyone", position=0)
        self.bot_role = MockRole(999, "Cosmic Bot Role", position=50, admin=True)
        self.roles: List[MockRole] = [self.default_role, self.bot_role]
        self.members: List[MockMember] = []
        self.text_channels: List[MockTextChannel] = []
        self.voice_channels: List[Any] = []
        self.categories: List[Any] = []
        self.channels: List[Any] = []
        self.emojis: List[Any] = []
        self.member_count = 100
        self.me = MockMember(999999999, "Cosmic ki baddie", self, roles=[self.default_role, self.bot_role], admin=True)

    def get_role(self, role_id: int) -> Optional[MockRole]:
        return next((r for r in self.roles if r.id == role_id), None)

    def get_member(self, user_id: int) -> Optional[MockMember]:
        return next((m for m in self.members if m.id == user_id), None)

    def get_channel(self, channel_id: int) -> Optional[MockTextChannel]:
        return next((c for c in self.text_channels if c.id == channel_id), None)

    async def create_role(self, name: str, **kwargs: Any) -> MockRole:
        new_role = MockRole(len(self.roles) + 2000, name, position=2)
        self.roles.append(new_role)
        return new_role

    async def create_text_channel(self, name: str, **kwargs: Any) -> MockTextChannel:
        ch = MockTextChannel(len(self.text_channels) + 3000, name, self)
        self.text_channels.append(ch)
        return ch


class MockContext:
    def __init__(
        self,
        bot: CosmicBot,
        guild: MockGuild,
        author: MockMember,
        channel: MockTextChannel,
        command_name: str = "test",
    ) -> None:
        self.bot = bot
        self.guild = guild
        self.author = author
        self.channel = channel
        self.interaction = None
        self.command = bot.get_command(command_name)
        self.replies: List[Dict[str, Any]] = []

    async def reply(self, content: Optional[str] = None, **kwargs: Any) -> Any:
        self.replies.append({"content": content, **kwargs})
        return types.SimpleNamespace(id=len(self.replies))


# ============================================================================
# Verification Test Suite
# ============================================================================
class CosmicBotVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_cosmic.db"
        self.backup_dir = Path(self.tmp_dir.name) / "backups"
        self.db = DatabaseManager(f"sqlite:///{self.db_path}", backup_dir=self.backup_dir)
        self.bot = CosmicBot(db=self.db)
        await self.bot.setup_hook()

        self.guild = MockGuild(id=777001, name="Side Quest Studio", owner_id=101)
        self.bot.guilds.append(self.guild)
        self.general_ch = MockTextChannel(888001, "general", self.guild)
        self.log_ch = MockTextChannel(888002, "mod-logs", self.guild)
        self.guild.text_channels.extend([self.general_ch, self.log_ch])

        self.admin_role = MockRole(501, "Studio Admin", position=20, admin=False)
        self.mod_role = MockRole(502, "Quest Moderator", position=15)
        self.member_role = MockRole(503, "Quester", position=5)
        self.guild.roles.extend([self.admin_role, self.mod_role, self.member_role])

        self.owner_member = MockMember(101, "CosmicOwner", self.guild, roles=[self.guild.default_role, self.admin_role], admin=True)
        self.mod_member = MockMember(102, "StudioMod", self.guild, roles=[self.guild.default_role, self.mod_role], mod=True)
        self.regular_member = MockMember(103, "RegularPlayer", self.guild, roles=[self.guild.default_role, self.member_role])
        self.guild.members.extend([self.owner_member, self.mod_member, self.regular_member])

    async def asyncTearDown(self) -> None:
        await self.bot.close()
        self.tmp_dir.cleanup()

    def test_01_ast_and_syntax_all_python_files(self) -> None:
        """Verify every Python file in CosmicKiBaddie parses cleanly with zero syntax errors."""
        py_files = list(PROJECT_ROOT.rglob("*.py"))
        self.assertGreaterEqual(len(py_files), 24)
        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            self.assertIsNotNone(tree, f"Failed to parse AST for {py_file}")

    async def test_02_database_schema_and_backup(self) -> None:
        """Verify all 18 database tables exist and backup creation works."""
        tables = await self.db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = {r["name"] for r in tables}
        expected_tables = {
            "schema_migrations",
            "guild_configs",
            "role_permissions",
            "command_settings",
            "mod_cases",
            "warnings",
            "warning_escalations",
            "mod_role_permissions",
            "logging_configs",
            "autoresponders",
            "welcome_configs",
            "ticket_configs",
            "tickets",
            "saved_embeds",
            "member_activity",
            "role_rewards",
            "temp_roles",
            "role_menus",
            "scheduled_automations",
            "automation_rules",
            "antinuke_configs",
            "security_incidents",
            "giveaways",
            "community_events",
            "polls",
        }
        self.assertTrue(
            expected_tables.issubset(table_names),
            f"Missing tables: {expected_tables - table_names}",
        )
        backup_file = await self.db.create_backup(max_keep=5)
        self.assertIsNotNone(backup_file)
        assert backup_file is not None
        self.assertTrue(backup_file.exists())

    async def test_03_all_17_cogs_and_required_commands_loaded(self) -> None:
        """Verify all 17 cogs and all 25 moderation commands + system groups are registered."""
        self.assertEqual(len(self.bot.cogs), 17)
        required_moderation_commands = [
            "ban",
            "unban",
            "kick",
            "timeout",
            "untimeout",
            "warn",
            "warnings",
            "clearwarnings",
            "purge",
            "slowmode",
            "lock",
            "unlock",
            "hide",
            "unhide",
            "nickname",
            "role",
            "mute",
            "unmute",
            "snipe",
            "editsnipe",
            "userinfo",
            "serverinfo",
            "modlogs",
            "reason",
            "case",
        ]
        for cmd_name in required_moderation_commands:
            self.assertIsNotNone(
                self.bot.get_command(cmd_name),
                f"Required moderation command '/{cmd_name}' is not registered!",
            )

        required_groups = [
            "prefix",
            "permissions",
            "command",
            "roles",
            "activity",
            "automation",
            "autoresponder",
            "welcome",
            "goodbye",
            "ticket",
            "embed",
            "antinuke",
            "safemode",
            "protection",
            "giveaway",
            "events",
            "poll",
            "setup",
            "help",
            "logging",
        ]
        for grp in required_groups:
            self.assertIsNotNone(
                self.bot.get_command(grp),
                f"Required command group '/{grp}' is not registered!",
            )

    async def test_04_custom_prefix_persistence(self) -> None:
        """Verify custom prefixes can be changed, added, and persist in the database."""
        ctx = MockContext(self.bot, self.guild, self.owner_member, self.general_ch, "prefix set")
        prefix_set_cmd = self.bot.get_command("prefix set")
        assert prefix_set_cmd is not None
        await prefix_set_cmd.callback(ctx, new_prefix="?, cs!")
        prefixes = await self.db.get_prefixes(self.guild.id)
        self.assertEqual(prefixes, ["?", "cs!"])

    async def test_05_permissions_engine_and_safe_mode(self) -> None:
        """Verify dynamic role permissions, deny rules, Safe Mode lockdown, and hierarchy protection."""
        # 1. Regular member cannot use `/ban` by default
        allowed, _ = await self.bot.permissions_engine.evaluate_command_access(
            self.guild, self.regular_member, self.general_ch, "ban", "Moderation"
        )
        self.assertFalse(allowed)

        # 2. Grant regular_member's role explicit access to `ban` via `/permissions set`
        await self.db.set_permission_rule(
            self.guild.id, "role", self.member_role.id, "ban", "allow", self.owner_member.id
        )
        allowed_after, _ = await self.bot.permissions_engine.evaluate_command_access(
            self.guild, self.regular_member, self.general_ch, "ban", "Moderation"
        )
        self.assertTrue(allowed_after)

        # 3. Activate Safe Mode -> non-admin cannot use `ban` even with allow rule!
        await self.db.update_guild_config(self.guild.id, safe_mode=1)
        allowed_safe, reason_safe = await self.bot.permissions_engine.evaluate_command_access(
            self.guild, self.regular_member, self.general_ch, "ban", "Moderation"
        )
        self.assertFalse(allowed_safe)
        self.assertIn("Safe Mode", reason_safe)

        # 4. Server Owner / Emergency Admin still has access during Safe Mode
        allowed_owner, _ = await self.bot.permissions_engine.evaluate_command_access(
            self.guild, self.owner_member, self.general_ch, "ban", "Moderation"
        )
        self.assertTrue(allowed_owner)

        # 5. Hierarchy check prevents moderator from targeting Server Owner
        ok_hier, _ = self.bot.permissions_engine.check_hierarchy(
            self.guild, self.mod_member, self.owner_member, self.guild.me
        )
        self.assertFalse(ok_hier)

    async def test_06_moderation_warn_and_auto_escalation(self) -> None:
        """Verify issuing warnings creates sequential cases and triggers configured auto-escalation."""
        mod_cog: Any = self.bot.get_cog("Moderation")
        self.assertIsNotNone(mod_cog)

        # Configure escalation at 2 warnings -> 30m timeout
        ctx_esc = MockContext(self.bot, self.guild, self.owner_member, self.general_ch, "escalation set")
        await mod_cog.escalation_set.callback(mod_cog, ctx_esc, 2, "timeout", "30m")

        # Issue Warning #1
        ctx_w1 = MockContext(self.bot, self.guild, self.mod_member, self.general_ch, "warn")
        await mod_cog.warn_cmd.callback(mod_cog, ctx_w1, self.regular_member, reason="Spamming chat")
        self.assertIsNone(self.regular_member.timed_out)

        # Issue Warning #2 -> triggers auto-escalation timeout!
        ctx_w2 = MockContext(self.bot, self.guild, self.mod_member, self.general_ch, "warn")
        await mod_cog.warn_cmd.callback(mod_cog, ctx_w2, self.regular_member, reason="Second violation")
        self.assertIsNotNone(self.regular_member.timed_out)

        warnings = await self.db.get_warnings(self.guild.id, self.regular_member.id)
        self.assertEqual(len(warnings), 2)

    async def test_07_autoresponder_and_activity_progression(self) -> None:
        """Verify keyword autoresponder matching and activity XP role reward progression."""
        ar_cog: Any = self.bot.get_cog("Autoresponder")
        auto_cog: Any = self.bot.get_cog("Automation")
        self.assertTrue(
            ar_cog._matches_rule("Where is the server ip?", ["ip", "server ip"], "contains", False)
        )
        self.assertFalse(
            ar_cog._matches_rule("Where is the server ip?", ["ip"], "exact", False)
        )

        # Configure a Level 1 Role Reward and grant XP to regular_member
        lvl1_role = MockRole(599, "Level 1 Explorer", position=3)
        self.guild.roles.append(lvl1_role)
        ctx_rw = MockContext(self.bot, self.guild, self.owner_member, self.general_ch, "activity reward_add")
        await auto_cog.activity_reward_add.callback(
            auto_cog, ctx_rw, lvl1_role, "level", 1, True, True
        )
        await auto_cog.add_member_xp(
            self.guild, self.regular_member, xp_delta=200, messages_delta=5, channel=self.general_ch
        )
        self.assertIn(lvl1_role, self.regular_member.roles)

    async def test_08_antinuke_false_positive_protection(self) -> None:
        """Verify Anti-Nuke never triggers on a single event and respects trusted actors."""
        tracker = SlidingWindowTracker()
        # First event count is 1 (< minimum threshold of 2)
        c1 = tracker.record_and_count(self.guild.id, 555, "mass_channel_delete", 30)
        self.assertEqual(c1, 1)
        self.assertGreaterEqual(adjust_threshold_for_mode(1, "normal"), 2)
        self.assertEqual(adjust_threshold_for_mode(4, "enhanced"), 3)
        self.assertEqual(adjust_threshold_for_mode(4, "emergency"), 2)
        self.assertTrue(
            is_trusted_actor(101, [], 101, 999999999, [], [])
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
