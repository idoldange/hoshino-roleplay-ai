"""
affection/bond.py
~~~~~~~~~~~~~~~~~
Per-user bond. Max 100. Loaded fully into RAM on startup.
DB writes happen async in background — never blocks response path.
"""

import asyncio
import random
import os
import aiosqlite
import threading
from config import AFFECTION_DB_PATH as DB_PATH
from logger import logger

# Async lock for DB operations
_lock = asyncio.Lock()
# Thread lock for sync cache access (protects _cache dict during reads/writes)
_cache_lock = threading.Lock()

from config import AFFECTION_BASE_EXP_MAX as BASE_EXP_MAX
from config import AFFECTION_BASE_EXP_MIN as BASE_EXP_MIN
from config import AFFECTION_RANKS as RANKS

# In-memory cache: {user_id: bond_float}
_cache: dict[int, float] = {}

# Track pending flush tasks to ensure they complete before shutdown
_pending_flushes: set = set()

# Minimum cache size threshold — if cache is this small, something went wrong
_EXPECTED_CACHE_SIZE = 70

# Model-generated bond events are intentionally small and infrequent.
MAX_EVENT_INCREASE = 25.0
MAX_EVENT_DECREASE = 5.0

# Helpers

def _rank_info(bond: float) -> tuple[str, float]:
    # RANKS format in config: (lo, hi, name, mult)
    # Blue Archive rank names for Arona (config.py):
    #   (  0,  10, "Unregistered",          1.00)
    #   ( 10,  25, "Momotalk: New Contact",  0.75)
    #   ( 25,  45, "Schale Associate",       0.55)
    #   ( 45,  60, "Trusted Sensei",         0.38)
    #   ( 60,  75, "Kivotos Partner",        0.25)
    #   ( 75,  90, "Shittim Core",           0.14)
    #   ( 90, 100, "Hoshino's Sensei",       0.07)
    #   (100, 101, "Navigator's Sensei",     0.00)  # cap
    for lo, hi, name, mult in RANKS:
        if lo <= bond < hi:
            return name, mult
    return "Navigator's Sensei", 0.0


def rank_name(bond: float) -> str:
    return _rank_info(bond)[0]


def exp_for_message(bond: float, mood: float = 0.0, mood_delta: float = 0.0) -> float:
    _, mult = _rank_info(bond)
    if mult == 0.0:
        logger.debug("[bond] exp_for_message: mult=0 (bond=%.2f is capped), returning 0", bond)
        return 0.0
    base = random.uniform(BASE_EXP_MIN, BASE_EXP_MAX)
    roll = random.random()
    if roll < 0.10:
        logger.debug("[bond] exp_for_message: 10%% chance hit, returning 0")
        return 0.0
    if roll > 0.95:
        base *= 1.8
    # Mood multiplier: mood 100 = 1.5x, mood -100 = 0.5x
    mood_mult = 1.0 + (mood / 200.0)
    # Bonus if user's message itself boosted mood
    delta_bonus = 1.0 + (mood_delta / 100.0) if mood_delta > 0 else 1.0
    result = round(base * mult * mood_mult * delta_bonus, 4)
    logger.debug("[bond] exp_for_message: bond=%.2f mood=%.2f -> exp=%.4f", bond, mood, result)
    return result


def prompt_line(bond: float) -> str:
    return f"Relationship with this user: {rank_name(bond)} (Bond {bond:.1f}/100)"


# DB

async def _init_db():
    db_dir = os.path.dirname(DB_PATH)
    os.makedirs(db_dir, exist_ok=True)
    logger.debug("[bond] Using database: %s", DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bond (
                user_id INTEGER PRIMARY KEY,
                bond    REAL    NOT NULL DEFAULT 0.0
            )
        """)
        await db.commit()


async def _flush(user_id: int, bond: float):
    """Write a single user's bond to DB in the background."""
    user_id = int(user_id)
    try:
        # Read latest value from cache — avoids writing stale value if cache was updated after this task was scheduled
        with _cache_lock:
            bond = _cache.get(user_id, bond)
        async with _lock:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
                    INSERT INTO bond (user_id, bond) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET bond = excluded.bond
                """, (user_id, bond))
                await db.commit()
                logger.debug("[bond] Saved user=%s bond=%.2f", user_id, bond)
    except Exception as e:
        logger.error("[bond] flush FAILED user=%s bond=%s: %s", user_id, bond, e)


async def _wait_all_flushes():
    """Wait for all pending flush tasks to complete. Call this before shutdown."""
    if _pending_flushes:
        logger.info("[bond] Waiting for %s flush tasks to complete...", len(_pending_flushes))
        await asyncio.gather(*_pending_flushes, return_exceptions=True)
        _pending_flushes.clear()
        logger.info("[bond] All flush tasks completed.")


# Public API

async def initialize():
    """Load all bond rows into RAM. Call once at startup."""
    logger.info("[bond] initialize() START")
    logger.debug("[bond] _cache object id at START: %s", id(_cache))
    await _init_db()
    logger.info("[bond] Loading bonds from: %s", DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, bond FROM bond") as cur:
            rows = await cur.fetchall()
            with _cache_lock:
                for user_id, bond in rows:
                    _cache[int(user_id)] = min(100.0, bond)
                    logger.debug("[bond] Loaded user=%s bond=%.2f", user_id, bond)
    with _cache_lock:
        size = len(_cache)
    logger.info("[bond] initialize() COMPLETE: %s users in cache, id=%s", size, id(_cache))


def get(user_id: int) -> float:
    """Sync read from RAM. Always instant. Thread-safe."""
    user_id = int(user_id)
    with _cache_lock:
        # Safe read with lock
        first_5_keys = list(_cache.keys())[:5]
        in_cache = user_id in _cache
        cache_size = len(_cache)
        logger.debug("[bond] get(%s): cache_size=%s, in_cache=%s, id=%s, sample_keys=%s", user_id, cache_size, in_cache, id(_cache), first_5_keys)
        
        # If user NOT in cache but cache exists, corruption detected
        if not in_cache and cache_size > 0:
            logger.debug("[bond] User %s not in cache with %s items. Triggering reload from DB", user_id, cache_size)
            all_keys = list(_cache.keys())
            logger.debug("[bond] Cache contents: %s", f"{all_keys[:20]}{'...' if len(all_keys) > 20 else ''}")
            # Note: Don't trigger reload while holding lock - release first
        
        # Safety check: if cache is suspiciously small, might need reload
        elif cache_size < _EXPECTED_CACHE_SIZE // 2 and user_id not in _cache:
            logger.warning("[bond] Cache is small (%s users)", cache_size)
        
        val = _cache.get(user_id, 0.0)
    
    # Trigger reload outside lock to avoid deadlock
    if not in_cache and cache_size > 0:
        asyncio.create_task(_reload_cache_from_db())
    
    logger.debug("[bond] get(%s): result=%.2f", user_id, val)
    return val


async def _reload_cache_from_db():
    """
    Emergency reload: fetch DB rows and insert missing users into cache.
    Does NOT overwrite existing entries — avoids clobbering values that were
    updated in RAM but not yet flushed to DB.
    """
    try:
        with _cache_lock:
            old_size = len(_cache)

        # Load from DB outside the lock to avoid holding lock during DB I/O
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id, bond FROM bond") as cur:
                rows = await cur.fetchall()

        # Merge: only add users not already present in cache
        with _cache_lock:
            added = 0
            for user_id, bond in rows:
                user_id = int(user_id)
                if user_id not in _cache:
                    _cache[user_id] = min(100.0, bond)
                    added += 1
            final_size = len(_cache)

        logger.info("[bond] _reload_cache_from_db() complete: added %s missing users, total=%s", added, final_size)
        if final_size == 0:
            logger.error("[bond] _reload_cache_from_db() restored 0 users! DB might be corrupted or locked")
    except Exception as e:
        logger.error("[bond] _reload_cache_from_db() FAILED: %s", e)


def add_and_get(user_id: int, delta: float) -> float:
    """
    Add delta to bond in RAM, schedule DB flush, return new value.
    Fully sync — no await needed in the hot path. Thread-safe.
    """
    user_id = int(user_id)
    with _cache_lock:
        logger.debug("[bond] add_and_get() START: user=%s, delta=%.2f, cache_size=%s", user_id, delta, len(_cache))
        current = _cache.get(user_id, 0.0)
        delta = max(-MAX_EVENT_DECREASE, min(MAX_EVENT_INCREASE, float(delta)))
        new_val = max(0.0, min(100.0, current + delta))
        _cache[user_id] = new_val
        cache_size = len(_cache)
        logger.debug("[bond] add_and_get: user=%s %+0.2f (was %.2f) -> %.2f, cache_size=%s", user_id, delta, current, new_val, cache_size)
    
    # Fire-and-forget DB write, but track it
    task = asyncio.create_task(_flush(user_id, new_val))
    _pending_flushes.add(task)
    # Remove from set when task completes
    task.add_done_callback(lambda t: _pending_flushes.discard(t))
    return new_val


async def add_and_get_immediate(user_id: int, delta: float) -> float:
    """
    Add delta to bond in RAM and IMMEDIATELY write to DB (blocking).
    Use this when you need guaranteed persistence (e.g., per message).
    """
    user_id = int(user_id)
    with _cache_lock:
        current = _cache.get(user_id, 0.0)
        delta = max(-MAX_EVENT_DECREASE, min(MAX_EVENT_INCREASE, float(delta)))
        new_val = max(0.0, min(100.0, current + delta))
        _cache[user_id] = new_val
        logger.debug("[bond] User %s: %+0.2f -> %.2f", user_id, delta, new_val)
    # Blocking DB write — await it
    await _flush(user_id, new_val)
    return new_val


async def set_bond(user_id: int, value: float):
    """Directly set bond (admin/debug use). Writes to RAM and DB."""
    user_id = int(user_id)
    value = max(0.0, min(100.0, value))
    with _cache_lock:
        _cache[user_id] = value
    await _flush(user_id, value)


async def clear_bond(user_id: int):
    """Clear a user's bond (set to 0). Writes to RAM and DB."""
    user_id = int(user_id)
    with _cache_lock:
        _cache[user_id] = 0.0
    await _flush(user_id, 0.0)


async def health_check_loop():
    """
    Periodic background task to verify cache integrity.
    Run this with: asyncio.create_task(affection.health_check_loop())
    Checks every 5 minutes if cache size is healthy. If corrupted, reloads from DB.
    """
    import time
    check_interval = 300  # 5 minutes
    with _cache_lock:
        last_size = len(_cache)
    
    while True:
        try:
            await asyncio.sleep(check_interval)
            with _cache_lock:
                current_size = len(_cache)
            
            # If cache shrunk significantly, might be corrupted
            if current_size < last_size * 0.8 and current_size > 0:  # Lost 20% of users
                logger.warning("[bond] HEALTH_CHECK: Cache size dropped from %s to %s. Possible corruption detected!", last_size, current_size)
                asyncio.create_task(_reload_cache_from_db())
            elif current_size == 0 and last_size > 0:
                logger.error("[bond] HEALTH_CHECK: Cache is EMPTY but should have %s users! Emergency reload!", last_size)
                asyncio.create_task(_reload_cache_from_db())
            else:
                logger.debug("[bond] HEALTH_CHECK: Cache healthy (%s users)", current_size)
                last_size = current_size
                
        except Exception as e:
            logger.error("[bond] health_check_loop error: %s", e)
            await asyncio.sleep(10)  # Brief wait before retry


async def shutdown():
    """Call this during bot shutdown to flush all pending bond updates."""
    await _wait_all_flushes()