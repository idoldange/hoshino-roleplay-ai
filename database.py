import asyncio
import math
import time
from datetime import datetime, timezone

import aiosqlite

import config as C


class Database:
    def __init__(self, path: str):
        self.path = path
        self._lock = asyncio.Lock()

    async def _ensure_daily_summary_schema(self):
        """Apply the daily-summary migration for databases created by older builds."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS daily_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    source_first_history_id INTEGER,
                    source_last_history_id INTEGER,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'completed',
                    prompt_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, window_start, window_end, content_hash)
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS daily_summary_compactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    source_first_summary_id INTEGER NOT NULL,
                    source_last_summary_id INTEGER NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    prompt_version TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, source_last_summary_id, content_hash)
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_summaries_user_window "
                "ON daily_summaries(user_id, window_start, window_end)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_summary_compactions_user "
                "ON daily_summary_compactions(user_id, window_end DESC)"
            )
            await db.commit()

    async def init(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA busy_timeout=5000;")
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_memory (
                    user_id INTEGER PRIMARY KEY,
                    memory TEXT NOT NULL DEFAULT '',
                    updated_at TEXT
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_personalization (
                    user_id INTEGER PRIMARY KEY,
                    guide TEXT NOT NULL DEFAULT '',
                    updated_at TEXT
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS auto_channels (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    added_at TEXT
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS reasoning_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    channel_id INTEGER,
                    reasoning TEXT,
                    created_at TEXT
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_reasoning_channel ON reasoning_log(channel_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS global_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT,
                    content TEXT,
                    channel_id INTEGER,
                    channel_name TEXT,
                    guild_id INTEGER,
                    guild_name TEXT,
                    created_at TEXT
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_global_history_user ON global_history(user_id)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY (user_id, key)
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_lust (
                    user_id INTEGER PRIMARY KEY,
                    lust_value REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT
                )"""
            )
            # Migration: tách Lust (cấp độ lâu dài) khỏi Arousal (hưng phấn tạm thời).
            async with db.execute("PRAGMA table_info(user_lust)") as _cur:
                _lust_cols = {_r[1] for _r in await _cur.fetchall()}
            for _col, _ddl in (
                ("arousal", "ALTER TABLE user_lust ADD COLUMN arousal REAL NOT NULL DEFAULT 0.0"),
                ("arousal_at", "ALTER TABLE user_lust ADD COLUMN arousal_at REAL"),
                ("lust_level", "ALTER TABLE user_lust ADD COLUMN lust_level INTEGER NOT NULL DEFAULT 0"),
                ("lust_day", "ALTER TABLE user_lust ADD COLUMN lust_day TEXT"),
                ("lust_gained", "ALTER TABLE user_lust ADD COLUMN lust_gained INTEGER NOT NULL DEFAULT 0"),
            ):
                if _col not in _lust_cols:
                    await db.execute(_ddl)
            await db.execute(
                """CREATE TABLE IF NOT EXISTS user_experience (
                    user_id INTEGER PRIMARY KEY,
                    experience_value REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT
                )"""
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS daily_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    source_first_history_id INTEGER,
                    source_last_history_id INTEGER,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    content_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'completed',
                    prompt_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, window_start, window_end, content_hash)
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_summaries_user_window "
                "ON daily_summaries(user_id, window_start, window_end)"
            )
            await db.execute(
                """CREATE TABLE IF NOT EXISTS daily_summary_compactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    source_first_summary_id INTEGER NOT NULL,
                    source_last_summary_id INTEGER NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    prompt_version TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, source_last_summary_id, content_hash)
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_summary_compactions_user "
                "ON daily_summary_compactions(user_id, window_end DESC)"
            )
            await db.commit()

    async def get_memory(self, user_id: int) -> str:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT memory FROM user_memory WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row["memory"] if row else ""

    async def set_memory(self, user_id: int, memory: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_memory (user_id, memory, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET memory=excluded.memory, updated_at=excluded.updated_at""",
                (user_id, memory.strip(), now),
            )
            await db.commit()

    async def get_personalization(self, user_id: int) -> str:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT guide FROM user_personalization WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return row["guide"] if row else ""

    async def set_personalization(self, user_id: int, guide: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_personalization (user_id, guide, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET guide=excluded.guide, updated_at=excluded.updated_at""",
                (user_id, guide.strip(), now),
            )
            await db.commit()

    async def clear_personalization(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM user_personalization WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_user_setting(self, user_id: int, key: str, default: str | None = None) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
            ) as cur:
                row = await cur.fetchone()
                return row["value"] if row else default

    async def set_user_setting(self, user_id: int, key: str, value: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_settings (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (user_id, key, value, now),
            )
            await db.commit()

    async def append_memory(self, user_id: int, note: str):
        current = await self.get_memory(user_id)
        combined = (current + "\n- " + note.strip()).strip() if current else "- " + note.strip()
        await self.set_memory(user_id, combined)

    async def apply_memory_diff(self, user_id: int, add: list[str] = None, remove: list[str] = None) -> str:
        """Cập nhật memory theo dạng diff: xoá các dòng khớp `remove`, rồi thêm các dòng mới trong `add`."""
        add = [a.strip() for a in (add or []) if a and a.strip()]
        remove = [r.strip() for r in (remove or []) if r and r.strip()]

        current = await self.get_memory(user_id)
        lines = [l for l in current.split("\n") if l.strip()] if current else []

        if remove:
            def _should_keep(line: str) -> bool:
                stripped = line.lstrip("- ").strip().lower()
                return not any(r.lower() in stripped for r in remove)

            lines = [l for l in lines if _should_keep(l)]

        for note in add:
            lines.append(note if note.startswith("-") else f"- {note}")

        new_memory = "\n".join(lines)
        await self.set_memory(user_id, new_memory)
        return new_memory

    async def clear_memory(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
            await db.commit()

    async def add_auto_channel(self, channel_id: int, guild_id: int):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO auto_channels (channel_id, guild_id, added_at) VALUES (?, ?, ?)",
                (channel_id, guild_id, now),
            )
            await db.commit()

    async def remove_auto_channel(self, channel_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("DELETE FROM auto_channels WHERE channel_id = ?", (channel_id,))
            await db.commit()
            return cur.rowcount > 0

    async def is_auto_channel(self, channel_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM auto_channels WHERE channel_id = ?", (channel_id,)
            ) as cur:
                return (await cur.fetchone()) is not None

    async def list_auto_channels(self, guild_id: int):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT channel_id FROM auto_channels WHERE guild_id = ?", (guild_id,)
            ) as cur:
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    async def save_reasoning(self, user_id: int, channel_id: int, reasoning: str):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO reasoning_log (user_id, channel_id, reasoning, created_at) VALUES (?, ?, ?, ?)",
                (user_id, channel_id, reasoning[:6000], now),
            )
            await db.execute(
                """DELETE FROM reasoning_log WHERE channel_id = ? AND id NOT IN (
                       SELECT id FROM reasoning_log WHERE channel_id = ? ORDER BY id DESC LIMIT 20
                   )""",
                (channel_id, channel_id),
            )
            await db.commit()

    async def get_last_reasoning(self, channel_id: int):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT reasoning, created_at FROM reasoning_log WHERE channel_id = ? ORDER BY id DESC LIMIT 1",
                (channel_id,),
            ) as cur:
                return await cur.fetchone()

    async def add_global_history(self, user_id: int, role: str, content: str, channel_id: int = None, channel_name: str = None, guild_id: int = None, guild_name: str = None, keep: int = 60):
        if not content or not content.strip():
            return
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO global_history (user_id, role, content, channel_id, channel_name, guild_id, guild_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, role, content.strip(), channel_id, channel_name, guild_id, guild_name, now),
            )
            await db.execute(
                """DELETE FROM global_history WHERE user_id = ? AND id NOT IN (
                       SELECT id FROM global_history WHERE user_id = ? ORDER BY id DESC LIMIT ?
                   )""",
                (user_id, user_id, keep),
            )
            await db.commit()

    async def get_global_history(self, user_id: int, limit: int = 30):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT role, content FROM global_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ) as cur:
                rows = await cur.fetchall()
                return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    async def search_global_history(
        self, user_id: int, query: str = None, channel_id: int = None, guild_id: int = None,
        channel_name: str = None, guild_name: str = None, limit: int = 10,
    ):
        conditions = ["user_id = ?"]
        params = [user_id]
        if channel_id is not None:
            conditions.append("channel_id = ?")
            params.append(channel_id)
        if guild_id is not None:
            conditions.append("guild_id = ?")
            params.append(guild_id)
        if channel_name:
            conditions.append("channel_name LIKE ?")
            params.append(f"%{channel_name}%")
        if guild_name:
            conditions.append("guild_name LIKE ?")
            params.append(f"%{guild_name}%")
        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")
        where = " AND ".join(conditions)
        params.append(limit)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT role, content, channel_id, channel_name, guild_id, guild_name, created_at FROM global_history WHERE {where} ORDER BY id DESC LIMIT ?",
                params,
            ) as cur:
                rows = await cur.fetchall()
                return [dict(r) for r in reversed(rows)]

    async def get_global_history_window(self, user_id: int, window_start: str, window_end: str):
        """Return raw history rows for one summarizer window, oldest first."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id, role, content, channel_id, channel_name, guild_id,
                          guild_name, created_at
                     FROM global_history
                    WHERE user_id = ? AND created_at >= ? AND created_at < ?
                    ORDER BY id ASC""",
                (user_id, window_start, window_end),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_global_history_user_ids(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT DISTINCT user_id FROM global_history WHERE user_id IS NOT NULL") as cur:
                return [row[0] for row in await cur.fetchall()]

    async def save_daily_summary(
        self,
        user_id: int,
        window_start: str,
        window_end: str,
        summary_json: str,
        content_hash: str,
        source_first_history_id: int | None = None,
        source_last_history_id: int | None = None,
        source_count: int = 0,
        token_count: int = 0,
        prompt_version: str | None = None,
    ) -> bool:
        """Persist a non-empty canonical summary; duplicate retries are no-ops."""
        if not summary_json.strip() or source_count <= 0:
            return False

        await self._ensure_daily_summary_schema()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO daily_summaries (
                       user_id, window_start, window_end,
                       source_first_history_id, source_last_history_id,
                       source_count, content_hash, summary_json, token_count,
                       status, prompt_version, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?)""",
                (
                    user_id,
                    window_start,
                    window_end,
                    source_first_history_id,
                    source_last_history_id,
                    source_count,
                    content_hash,
                    summary_json.strip(),
                    token_count,
                    prompt_version,
                    now,
                    now,
                ),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_daily_summary_window(self, user_id: int, window_start: str, window_end: str):
        await self._ensure_daily_summary_schema()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id, content_hash, source_count, token_count, status
                     FROM daily_summaries
                    WHERE user_id = ? AND window_start = ? AND window_end = ?
                    ORDER BY id DESC LIMIT 1""",
                (user_id, window_start, window_end),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_latest_daily_summary(self, user_id: int):
        await self._ensure_daily_summary_schema()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM daily_summaries
                    WHERE user_id = ? AND status = 'completed'
                    ORDER BY window_end DESC, id DESC LIMIT 1""",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def get_daily_summaries_older_than(self, user_id: int, cutoff: str, after_id: int = 0):
        await self._ensure_daily_summary_schema()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM daily_summaries
                    WHERE user_id = ? AND window_end <= ? AND status = 'completed' AND id > ?
                    ORDER BY window_start ASC, id ASC""",
                (user_id, cutoff, after_id),
            ) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def get_latest_daily_compaction(self, user_id: int):
        await self._ensure_daily_summary_schema()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM daily_summary_compactions
                    WHERE user_id = ? ORDER BY source_last_summary_id DESC, id DESC LIMIT 1""",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def save_daily_compaction(
        self, user_id: int, source_first_summary_id: int, source_last_summary_id: int,
        window_start: str, window_end: str, summary_json: str, content_hash: str,
        token_count: int = 0, prompt_version: str | None = None,
    ) -> bool:
        if not summary_json.strip():
            return False
        await self._ensure_daily_summary_schema()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO daily_summary_compactions (
                    user_id, source_first_summary_id, source_last_summary_id,
                    window_start, window_end, content_hash, summary_json,
                    token_count, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, source_first_summary_id, source_last_summary_id,
                 window_start, window_end, content_hash, summary_json.strip(),
                 token_count, prompt_version, now),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get_daily_summary_context(self, user_id: int, keep_latest: int = 3):
        """Return one old-period compaction plus the newest daily summaries."""
        await self._ensure_daily_summary_schema()
        compaction = await self.get_latest_daily_compaction(user_id)
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                                """SELECT summary.* FROM daily_summaries summary
                                        WHERE summary.user_id = ? AND summary.status = 'completed'
                                            AND summary.id IN (
                                                SELECT MAX(id) FROM daily_summaries latest
                                                 WHERE latest.user_id = summary.user_id
                                                     AND latest.window_end = summary.window_end
                                                     AND latest.status = 'completed'
                                            )
                                        ORDER BY summary.window_end DESC, summary.id DESC LIMIT ?""",
                                (user_id, keep_latest),
            ) as cur:
                latest = [dict(row) for row in reversed(await cur.fetchall())]
        return ([compaction] if compaction else []) + latest

    async def clear_global_history(self, user_id: int):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM global_history WHERE user_id = ?", (user_id,))
            await db.commit()

    # Lust (persistent level) + Arousal (transient)
    async def get_arousal(self, user_id: int) -> float:
        """Arousal 0..100 with lazy exponential decay (half-life from config)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT arousal, arousal_at FROM user_lust WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return 0.0
        value = float(row["arousal"] or 0.0)
        at = row["arousal_at"]
        if value > 0 and at:
            dt = max(0.0, time.time() - float(at))
            value *= math.pow(0.5, dt / float(C.AROUSAL_HALFLIFE))
        return max(0.0, min(float(C.AROUSAL_MAX), value))

    async def set_arousal(self, user_id: int, value: float):
        """Set arousal directly (writes the decay clock)."""
        value = max(0.0, min(float(C.AROUSAL_MAX), float(value)))
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_lust (user_id, arousal, arousal_at, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET arousal=excluded.arousal,
                       arousal_at=excluded.arousal_at, updated_at=excluded.updated_at""",
                (user_id, value, now, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def add_arousal(self, user_id: int, amount: float) -> float:
        """Add to the decayed arousal and clamp to 0..100. Returns the new value."""
        value = max(0.0, min(float(C.AROUSAL_MAX), await self.get_arousal(user_id) + float(amount)))
        await self.set_arousal(user_id, value)
        return value

    async def reset_arousal(self, user_id: int):
        """Reset transient arousal to 0 (climax)."""
        await self.set_arousal(user_id, 0.0)

    async def get_lust_level(self, user_id: int) -> int:
        """Persistent lust level 0..LUST_MAX_LEVEL (optional very slow decay)."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT lust_level, updated_at FROM user_lust WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return 0
        level = int(row["lust_level"] or 0)
        rate = float(C.LUST_DECAY_PER_DAY)
        at = row["updated_at"]
        if rate > 0 and level > 0 and at:
            try:
                days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() / 86400.0)
                level -= int(days * rate)
            except Exception:
                pass
        return max(0, min(int(C.LUST_MAX_LEVEL), level))

    async def set_lust_level(self, user_id: int, level: int):
        """Set the persistent lust level directly."""
        level = max(0, min(int(C.LUST_MAX_LEVEL), int(level)))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_lust (user_id, lust_level, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET lust_level=excluded.lust_level, updated_at=excluded.updated_at""",
                (user_id, level, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def add_lust_level(self, user_id: int, delta: int) -> int:
        """Change the persistent level (per-tag and per-day clamps). Returns the new level."""
        delta = max(-int(C.LUST_TAG_MAX), min(int(C.LUST_TAG_MAX), int(delta)))
        if delta == 0:
            return await self.get_lust_level(user_id)
        day = datetime.now(timezone.utc).date().isoformat()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT lust_level, lust_day, lust_gained FROM user_lust WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
            current = int(row["lust_level"] or 0) if row else 0
            gained = int(row["lust_gained"] or 0) if row and row["lust_day"] == day else 0
            if delta > 0:
                room = int(C.LUST_DAILY_CAP) - gained
                if room <= 0:
                    return current
                delta = min(delta, room)
            new_level = max(0, min(int(C.LUST_MAX_LEVEL), current + delta))
            applied = new_level - current
            if delta > 0 and applied > 0:
                gained += applied
            await db.execute(
                """INSERT INTO user_lust (user_id, lust_level, lust_day, lust_gained, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET lust_level=excluded.lust_level,
                       lust_day=excluded.lust_day, lust_gained=excluded.lust_gained,
                       updated_at=excluded.updated_at""",
                (user_id, new_level, day, gained, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        return new_level

    async def clear_lust(self, user_id: int):
        """Clear all lust/arousal data for a user."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM user_lust WHERE user_id = ?", (user_id,))
            await db.commit()

    # Experience table
    async def get_experience(self, user_id: int) -> float:
        """Get experience value."""
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT experience_value FROM user_experience WHERE user_id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
                return float(row["experience_value"]) if row else 0.0

    async def set_experience(self, user_id: int, experience_value: float):
        """Set experience value directly."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO user_experience (user_id, experience_value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET experience_value=excluded.experience_value, updated_at=excluded.updated_at""",
                (user_id, max(0.0, experience_value), now),
            )
            await db.commit()

    async def increment_experience(self, user_id: int, amount: float):
        """Increment experience with clamping to >= 0."""
        current = await self.get_experience(user_id)
        new_value = max(0.0, current + amount)
        await self.set_experience(user_id, new_value)

    async def clear_experience(self, user_id: int):
        """Clear experience for a user."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM user_experience WHERE user_id = ?", (user_id,))
            await db.commit()
