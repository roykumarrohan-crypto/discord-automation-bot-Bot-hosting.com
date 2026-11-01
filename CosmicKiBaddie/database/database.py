"""
Cosmic ki baddie — Asynchronous Database Manager
Supports SQLite (via `aiosqlite` or built-in async threadpool over `sqlite3`) and PostgreSQL (via `asyncpg`).
Provides automatic schema migrations, JSON serialization helpers, backups, and domain repositories.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("CosmicBot.Database")

try:
    import aiosqlite  # type: ignore

    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

try:
    import asyncpg  # type: ignore

    HAS_ASYNCPG = True
except ImportError:
    HAS_ASYNCPG = False


def utcnow_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class DatabaseManager:
    """
    Unified asynchronous relational database layer for Cosmic ki baddie.
    Works seamlessly with SQLite (local/VPS/Railway volume) and PostgreSQL (`postgresql://...`).
    """

    def __init__(self, database_url: str, backup_dir: Optional[Path] = None) -> None:
        self.database_url = database_url
        self.backup_dir = backup_dir or (Path(__file__).resolve().parent.parent / "data" / "backups")
        self.is_postgres = database_url.startswith(("postgres://", "postgresql://"))
        self.sqlite_path: Optional[Path] = None
        self._sqlite_conn: Optional[sqlite3.Connection] = None
        self._aiosqlite_conn: Any = None
        self._pg_pool: Any = None
        self._lock = asyncio.Lock()

        if not self.is_postgres:
            raw_path = database_url.replace("sqlite:///", "").replace("sqlite://", "")
            self.sqlite_path = Path(raw_path).resolve()
            self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Connect to the configured database backend and apply all SQL migrations."""
        if self.is_postgres:
            if not HAS_ASYNCPG:
                raise RuntimeError(
                    "PostgreSQL URL configured in DATABASE_URL, but 'asyncpg' is not installed."
                )
            self._pg_pool = await asyncpg.create_pool(dsn=self.database_url, min_size=2, max_size=10)
            logger.info("Connected to PostgreSQL connection pool.")
        else:
            assert self.sqlite_path is not None
            if HAS_AIOSQLITE:
                self._aiosqlite_conn = await aiosqlite.connect(str(self.sqlite_path))
                self._aiosqlite_conn.row_factory = aiosqlite.Row
                try:
                    await self._aiosqlite_conn.execute("PRAGMA journal_mode=WAL;")
                except Exception:
                    await self._aiosqlite_conn.execute("PRAGMA journal_mode=DELETE;")
                await self._aiosqlite_conn.execute("PRAGMA synchronous=NORMAL;")
                await self._aiosqlite_conn.execute("PRAGMA foreign_keys=ON;")
                await self._aiosqlite_conn.commit()
                logger.info("Connected to SQLite via aiosqlite at %s", self.sqlite_path)
            else:
                def _open_sqlite() -> sqlite3.Connection:
                    conn = sqlite3.connect(str(self.sqlite_path), check_same_thread=False)
                    conn.row_factory = sqlite3.Row
                    try:
                        conn.execute("PRAGMA journal_mode=WAL;")
                    except sqlite3.OperationalError:
                        conn.execute("PRAGMA journal_mode=DELETE;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.execute("PRAGMA foreign_keys=ON;")
                    conn.commit()
                    return conn

                self._sqlite_conn = await asyncio.to_thread(_open_sqlite)
                logger.info("Connected to SQLite (threaded async adapter) at %s", self.sqlite_path)

        await self.run_migrations()

    async def close(self) -> None:
        """Gracefully close open database connections."""
        if self._pg_pool is not None:
            await self._pg_pool.close()
            self._pg_pool = None
        if self._aiosqlite_conn is not None:
            await self._aiosqlite_conn.close()
            self._aiosqlite_conn = None
        if self._sqlite_conn is not None:
            conn = self._sqlite_conn
            self._sqlite_conn = None
            await asyncio.to_thread(conn.close)

    @staticmethod
    def _convert_qmarks_to_pg(query: str) -> str:
        """Convert SQLite `?` positional parameters to PostgreSQL `$1, $2, ...` syntax."""
        idx = 0

        def _repl(_: re.Match[str]) -> str:
            nonlocal idx
            idx += 1
            return f"${idx}"

        return re.sub(r"\?", _repl, query)

    @staticmethod
    def _adapt_ddl_for_postgres(sql_script: str) -> str:
        """Adapt SQLite DDL types (`INTEGER PRIMARY KEY AUTOINCREMENT`, `INTEGER`) to PostgreSQL (`BIGSERIAL`, `BIGINT`)."""
        adapted = sql_script.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
        )
        adapted = re.sub(r"\b(\w+_id)\s+INTEGER\b", r"\1 BIGINT", adapted)
        return adapted

    async def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        """Execute a mutating SQL statement and return the last inserted row ID or row count."""
        async with self._lock:
            if self.is_postgres:
                pg_query = self._convert_qmarks_to_pg(query)
                async with self._pg_pool.acquire() as conn:
                    await conn.execute(pg_query, *params)
                    return 0
            if self._aiosqlite_conn is not None:
                async with self._aiosqlite_conn.execute(query, params) as cursor:
                    last_id = cursor.lastrowid or 0
                await self._aiosqlite_conn.commit()
                return int(last_id)
            assert self._sqlite_conn is not None

            def _sync_exec() -> int:
                cur = self._sqlite_conn.execute(query, params)
                self._sqlite_conn.commit()
                return int(cur.lastrowid or 0)

            return await asyncio.to_thread(_sync_exec)

    async def fetchone(self, query: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        """Fetch a single row as a dictionary, or None if not found."""
        async with self._lock:
            if self.is_postgres:
                pg_query = self._convert_qmarks_to_pg(query)
                async with self._pg_pool.acquire() as conn:
                    row = await conn.fetchrow(pg_query, *params)
                    return dict(row) if row is not None else None
            if self._aiosqlite_conn is not None:
                async with self._aiosqlite_conn.execute(query, params) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row is not None else None
            assert self._sqlite_conn is not None

            def _sync_fetchone() -> Optional[Dict[str, Any]]:
                cur = self._sqlite_conn.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row is not None else None

            return await asyncio.to_thread(_sync_fetchone)

    async def fetchall(self, query: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        """Fetch all matching rows as a list of dictionaries."""
        async with self._lock:
            if self.is_postgres:
                pg_query = self._convert_qmarks_to_pg(query)
                async with self._pg_pool.acquire() as conn:
                    rows = await conn.fetch(pg_query, *params)
                    return [dict(r) for r in rows]
            if self._aiosqlite_conn is not None:
                async with self._aiosqlite_conn.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(r) for r in rows]
            assert self._sqlite_conn is not None

            def _sync_fetchall() -> List[Dict[str, Any]]:
                cur = self._sqlite_conn.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

            return await asyncio.to_thread(_sync_fetchall)

    async def executescript(self, script: str) -> None:
        """Execute a multi-statement SQL script."""
        async with self._lock:
            if self.is_postgres:
                pg_script = self._adapt_ddl_for_postgres(script)
                async with self._pg_pool.acquire() as conn:
                    await conn.execute(pg_script)
                return
            if self._aiosqlite_conn is not None:
                await self._aiosqlite_conn.executescript(script)
                await self._aiosqlite_conn.commit()
                return
            assert self._sqlite_conn is not None

            def _sync_script() -> None:
                self._sqlite_conn.executescript(script)
                self._sqlite_conn.commit()

            await asyncio.to_thread(_sync_script)

    async def run_migrations(self) -> None:
        """Discover and apply any unapplied `.sql` files in `database/migrations/`."""
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        sql_files = sorted(migrations_dir.glob("*.sql"))
        for sql_file in sql_files:
            content = sql_file.read_text(encoding="utf-8")
            await self.executescript(content)
            version_match = re.match(r"^(\d+)", sql_file.stem)
            version = int(version_match.group(1)) if version_match else 1
            existing = await self.fetchone(
                "SELECT version FROM schema_migrations WHERE version = ?", (version,)
            )
            if not existing:
                await self.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, sql_file.name, utcnow_iso()),
                )
                logger.info("Applied database migration %s (v%s)", sql_file.name, version)

    # =========================================================================
    # Backup & Restore Utilities
    # =========================================================================
    async def create_backup(self, max_keep: int = 15) -> Optional[Path]:
        """Create a timestamped backup copy of the SQLite database file and prune old backups."""
        if self.is_postgres or not self.sqlite_path or not self.sqlite_path.exists():
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"cosmic_ki_baddie_{timestamp}.db"

        def _copy() -> None:
            shutil.copy2(str(self.sqlite_path), str(backup_file))
            backups = sorted(self.backup_dir.glob("cosmic_ki_baddie_*.db"))
            while len(backups) > max_keep:
                oldest = backups.pop(0)
                oldest.unlink(missing_ok=True)

        await asyncio.to_thread(_copy)
        logger.info("Created SQLite backup at %s", backup_file)
        return backup_file

    # =========================================================================
    # 1. Guild Config & Prefix Helpers
    # =========================================================================
    async def ensure_guild(self, guild_id: int, default_prefix: str = "!") -> Dict[str, Any]:
        """Fetch or initialize the configuration row for a guild."""
        row = await self.fetchone("SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,))
        if row is None:
            now = utcnow_iso()
            await self.execute(
                """
                INSERT INTO guild_configs (guild_id, prefixes_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, json.dumps([default_prefix]), now, now),
            )
            row = await self.fetchone("SELECT * FROM guild_configs WHERE guild_id = ?", (guild_id,))
        assert row is not None
        return row

    async def get_prefixes(self, guild_id: int, default_prefix: str = "!") -> List[str]:
        """Return the list of configured command prefixes for a guild."""
        row = await self.ensure_guild(guild_id, default_prefix)
        try:
            prefixes = json.loads(row.get("prefixes_json") or '["!"]')
            return prefixes if isinstance(prefixes, list) and prefixes else [default_prefix]
        except Exception:
            return [default_prefix]

    async def set_prefixes(self, guild_id: int, prefixes: List[str]) -> List[str]:
        """Persist one or more prefixes for a guild."""
        cleaned = [p.strip() for p in prefixes if p and p.strip()][:10]
        if not cleaned:
            cleaned = ["!"]
        await self.ensure_guild(guild_id)
        await self.execute(
            "UPDATE guild_configs SET prefixes_json = ?, updated_at = ? WHERE guild_id = ?",
            (json.dumps(cleaned), utcnow_iso(), guild_id),
        )
        return cleaned

    async def update_guild_config(self, guild_id: int, **fields: Any) -> Dict[str, Any]:
        """Update specific columns in `guild_configs` for a guild."""
        await self.ensure_guild(guild_id)
        if not fields:
            return await self.ensure_guild(guild_id)
        fields["updated_at"] = utcnow_iso()
        columns = ", ".join(f"{k} = ?" for k in fields.keys())
        values = list(fields.values()) + [guild_id]
        await self.execute(f"UPDATE guild_configs SET {columns} WHERE guild_id = ?", values)
        return await self.ensure_guild(guild_id)

    # =========================================================================
    # 2. Role & User Permission Manager Helpers
    # =========================================================================
    async def set_permission_rule(
        self,
        guild_id: int,
        target_type: str,
        target_id: int,
        command_name: str,
        effect: str,
        created_by: int,
    ) -> None:
        """Create or update an allow/deny permission rule for a role or user on a command."""
        command_key = command_name.lower().strip()
        existing = await self.fetchone(
            """
            SELECT id FROM role_permissions
            WHERE guild_id = ? AND target_type = ? AND target_id = ? AND command_name = ?
            """,
            (guild_id, target_type, target_id, command_key),
        )
        if existing:
            await self.execute(
                "UPDATE role_permissions SET effect = ?, created_by = ?, created_at = ? WHERE id = ?",
                (effect, created_by, utcnow_iso(), existing["id"]),
            )
        else:
            await self.execute(
                """
                INSERT INTO role_permissions (guild_id, target_type, target_id, command_name, effect, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, target_type, target_id, command_key, effect, created_by, utcnow_iso()),
            )

    async def remove_permission_rule(
        self, guild_id: int, target_type: str, target_id: int, command_name: str
    ) -> None:
        """Delete a permission rule for a role or user."""
        await self.execute(
            """
            DELETE FROM role_permissions
            WHERE guild_id = ? AND target_type = ? AND target_id = ? AND command_name = ?
            """,
            (guild_id, target_type, target_id, command_name.lower().strip()),
        )

    async def get_permission_rules(
        self, guild_id: int, command_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List all permission rules for a guild (or filtered by command)."""
        if command_name:
            return await self.fetchall(
                "SELECT * FROM role_permissions WHERE guild_id = ? AND command_name = ? ORDER BY id ASC",
                (guild_id, command_name.lower().strip()),
            )
        return await self.fetchall(
            "SELECT * FROM role_permissions WHERE guild_id = ? ORDER BY command_name ASC",
            (guild_id,),
        )

    async def reset_permission_rules(self, guild_id: int) -> None:
        """Clear all custom permission rules for a guild."""
        await self.execute("DELETE FROM role_permissions WHERE guild_id = ?", (guild_id,))

    # =========================================================================
    # 3. Dynamic Command Settings Helpers
    # =========================================================================
    async def get_command_setting(self, guild_id: int, command_name: str) -> Dict[str, Any]:
        """Get or return default runtime settings for a command in a guild."""
        key = command_name.lower().strip()
        row = await self.fetchone(
            "SELECT * FROM command_settings WHERE guild_id = ? AND command_name = ?",
            (guild_id, key),
        )
        if row is None:
            return {
                "guild_id": guild_id,
                "command_name": key,
                "enabled": 1,
                "category_enabled": 1,
                "cooldown_seconds": 0,
                "allowed_roles": [],
                "denied_roles": [],
                "allowed_channels": [],
                "denied_channels": [],
                "aliases": [],
            }
        return {
            "guild_id": row["guild_id"],
            "command_name": row["command_name"],
            "enabled": bool(row["enabled"]),
            "category_enabled": bool(row["category_enabled"]),
            "cooldown_seconds": int(row["cooldown_seconds"]),
            "allowed_roles": json.loads(row["allowed_roles_json"] or "[]"),
            "denied_roles": json.loads(row["denied_roles_json"] or "[]"),
            "allowed_channels": json.loads(row["allowed_channels_json"] or "[]"),
            "denied_channels": json.loads(row["denied_channels_json"] or "[]"),
            "aliases": json.loads(row["aliases_json"] or "[]"),
        }

    async def upsert_command_setting(
        self, guild_id: int, command_name: str, **updates: Any
    ) -> Dict[str, Any]:
        """Insert or update dynamic settings for a command."""
        current = await self.get_command_setting(guild_id, command_name)
        current.update(updates)
        key = command_name.lower().strip()
        existing = await self.fetchone(
            "SELECT command_name FROM command_settings WHERE guild_id = ? AND command_name = ?",
            (guild_id, key),
        )
        now = utcnow_iso()
        params = (
            int(bool(current["enabled"])),
            int(bool(current["category_enabled"])),
            int(current["cooldown_seconds"]),
            json.dumps(current["allowed_roles"]),
            json.dumps(current["denied_roles"]),
            json.dumps(current["allowed_channels"]),
            json.dumps(current["denied_channels"]),
            json.dumps(current["aliases"]),
            now,
        )
        if existing:
            await self.execute(
                """
                UPDATE command_settings
                SET enabled = ?, category_enabled = ?, cooldown_seconds = ?,
                    allowed_roles_json = ?, denied_roles_json = ?,
                    allowed_channels_json = ?, denied_channels_json = ?,
                    aliases_json = ?, updated_at = ?
                WHERE guild_id = ? AND command_name = ?
                """,
                (*params, guild_id, key),
            )
        else:
            await self.execute(
                """
                INSERT INTO command_settings (
                    guild_id, command_name, enabled, category_enabled, cooldown_seconds,
                    allowed_roles_json, denied_roles_json, allowed_channels_json,
                    denied_channels_json, aliases_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, key, *params),
            )
        return await self.get_command_setting(guild_id, key)

    # =========================================================================
    # 4. Moderation Cases & Warnings Helpers
    # =========================================================================
    async def create_mod_case(
        self,
        guild_id: int,
        action: str,
        target_id: int,
        target_name: str,
        moderator_id: int,
        moderator_name: str,
        reason: str,
        duration_seconds: Optional[int] = None,
        log_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a sequentially numbered moderation case for a guild."""
        max_row = await self.fetchone(
            "SELECT MAX(case_number) AS max_case FROM mod_cases WHERE guild_id = ?",
            (guild_id,),
        )
        next_case = int((max_row or {}).get("max_case") or 0) + 1
        now = utcnow_iso()
        await self.execute(
            """
            INSERT INTO mod_cases (
                guild_id, case_number, action, target_id, target_name,
                moderator_id, moderator_name, reason, duration_seconds,
                active, log_message_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                guild_id,
                next_case,
                action,
                target_id,
                target_name,
                moderator_id,
                moderator_name,
                reason,
                duration_seconds,
                log_message_id,
                now,
            ),
        )
        row = await self.fetchone(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND case_number = ?",
            (guild_id, next_case),
        )
        assert row is not None
        return row

    async def get_mod_case(self, guild_id: int, case_number: int) -> Optional[Dict[str, Any]]:
        """Retrieve a specific moderation case by guild and case number."""
        return await self.fetchone(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND case_number = ?",
            (guild_id, case_number),
        )

    async def update_mod_case_reason(
        self, guild_id: int, case_number: int, new_reason: str
    ) -> Optional[Dict[str, Any]]:
        """Update the reason on an existing moderation case."""
        await self.execute(
            "UPDATE mod_cases SET reason = ? WHERE guild_id = ? AND case_number = ?",
            (new_reason, guild_id, case_number),
        )
        return await self.get_mod_case(guild_id, case_number)

    async def get_user_modlogs(
        self, guild_id: int, target_id: Optional[int] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return recent moderation cases for a guild or specific member."""
        if target_id is not None:
            return await self.fetchall(
                "SELECT * FROM mod_cases WHERE guild_id = ? AND target_id = ? ORDER BY case_number DESC LIMIT ?",
                (guild_id, target_id, limit),
            )
        return await self.fetchall(
            "SELECT * FROM mod_cases WHERE guild_id = ? ORDER BY case_number DESC LIMIT ?",
            (guild_id, limit),
        )

    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        case_number: int,
        reason: str,
    ) -> int:
        """Record an active warning and return total active warning count for that member."""
        await self.execute(
            """
            INSERT INTO warnings (guild_id, user_id, moderator_id, case_number, reason, active, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (guild_id, user_id, moderator_id, case_number, reason, utcnow_iso()),
        )
        count_row = await self.fetchone(
            "SELECT COUNT(*) AS cnt FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )
        return int((count_row or {}).get("cnt") or 0)

    async def get_warnings(
        self, guild_id: int, user_id: int, active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Fetch warnings for a user."""
        if active_only:
            return await self.fetchall(
                "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY id DESC",
                (guild_id, user_id),
            )
        return await self.fetchall(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (guild_id, user_id),
        )

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        """Mark all active warnings for a user as inactive and return how many were cleared."""
        active = await self.get_warnings(guild_id, user_id, active_only=True)
        await self.execute(
            "UPDATE warnings SET active = 0 WHERE guild_id = ? AND user_id = ? AND active = 1",
            (guild_id, user_id),
        )
        return len(active)

    # =========================================================================
    # 5. Logging Config Helpers
    # =========================================================================
    async def ensure_logging_config(self, guild_id: int) -> Dict[str, Any]:
        """Get or create the logging configuration for a guild."""
        row = await self.fetchone(
            "SELECT * FROM logging_configs WHERE guild_id = ?", (guild_id,)
        )
        if row is None:
            await self.execute(
                "INSERT INTO logging_configs (guild_id) VALUES (?)", (guild_id,)
            )
            row = await self.fetchone(
                "SELECT * FROM logging_configs WHERE guild_id = ?", (guild_id,)
            )
        assert row is not None
        return row

    # =========================================================================
    # 6. Welcome & Goodbye Config Helpers
    # =========================================================================
    async def ensure_welcome_config(self, guild_id: int) -> Dict[str, Any]:
        """Get or create welcome/goodbye configuration for a guild."""
        row = await self.fetchone(
            "SELECT * FROM welcome_configs WHERE guild_id = ?", (guild_id,)
        )
        if row is None:
            await self.execute(
                "INSERT INTO welcome_configs (guild_id) VALUES (?)", (guild_id,)
            )
            row = await self.fetchone(
                "SELECT * FROM welcome_configs WHERE guild_id = ?", (guild_id,)
            )
        assert row is not None
        return row

    # =========================================================================
    # 7. Ticket Config Helpers
    # =========================================================================
    async def ensure_ticket_config(self, guild_id: int) -> Dict[str, Any]:
        """Get or create ticket system configuration for a guild."""
        row = await self.fetchone(
            "SELECT * FROM ticket_configs WHERE guild_id = ?", (guild_id,)
        )
        if row is None:
            await self.execute(
                "INSERT INTO ticket_configs (guild_id) VALUES (?)", (guild_id,)
            )
            row = await self.fetchone(
                "SELECT * FROM ticket_configs WHERE guild_id = ?", (guild_id,)
            )
        assert row is not None
        return row

    # =========================================================================
    # 8. Anti-Nuke Config Helpers
    # =========================================================================
    async def ensure_antinuke_config(self, guild_id: int) -> Dict[str, Any]:
        """Get or create anti-nuke configuration for a guild."""
        row = await self.fetchone(
            "SELECT * FROM antinuke_configs WHERE guild_id = ?", (guild_id,)
        )
        if row is None:
            await self.execute(
                "INSERT INTO antinuke_configs (guild_id) VALUES (?)", (guild_id,)
            )
            row = await self.fetchone(
                "SELECT * FROM antinuke_configs WHERE guild_id = ?", (guild_id,)
            )
        assert row is not None
        return row
