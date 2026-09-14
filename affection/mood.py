"""
affection/mood.py
~~~~~~~~~~~~~~~~~
Hoshino's global mood — a single float in [-100, 100].

Sleep / idle model:
  0 – 3 min idle  : active, small random drift only
  3 – 5 min idle  : drowsy, mood dips slightly (restless/bored)
  5+ min idle     : asleep — mood regens each tick, capped at HAPPY_CAP (40)

Temperature:
  Cool (< TEMP_COOL) → small mood boost
  Warm (> TEMP_WARM) → mood penalty
  Hot  (> TEMP_HOT)  → larger penalty (overheating)
  In between         → neutral
"""

import asyncio
import random
import os
import aiosqlite

from config import AFFECTION_DB_PATH
from config import (
    AFFECTION_DECAY_PER_TICK as DECAY_PER_TICK,
    AFFECTION_DB_FLUSH_EVERY_N as DB_FLUSH_EVERY_N,
    AFFECTION_DRIFT_STD as DRIFT_STD,
    AFFECTION_HAPPY_CAP as HAPPY_CAP,
    AFFECTION_MOOD_LEVELS as MOOD_LEVELS,
    AFFECTION_SHOCKED_DURATION_SECONDS as SHOCKED_DURATION_SECONDS,
    AFFECTION_AROUSED_BOND_THRESHOLD as AROUSED_BOND_THRESHOLD,
    AFFECTION_AROUSED_COOLDOWN_SECONDS as AROUSED_COOLDOWN_SECONDS,
    AFFECTION_AROUSED_MODEL_DURATION_SECONDS as AROUSED_MODEL_DURATION_SECONDS,
    AFFECTION_AROUSED_RANDOM_DURATION_SECONDS as AROUSED_RANDOM_DURATION_SECONDS,
    AFFECTION_AROUSED_RANDOM_LEVEL1_CHANCE as AROUSED_RANDOM_LEVEL1_CHANCE,
    AFFECTION_AROUSED_RANDOM_LEVEL2_CHANCE as AROUSED_RANDOM_LEVEL2_CHANCE,
    AFFECTION_SLEEP_AFTER as SLEEP_AFTER,
    AFFECTION_SLEEP_REGEN_PER_TICK as SLEEP_REGEN_PER_TICK,
    AFFECTION_TEMP_COOL as TEMP_COOL,
    AFFECTION_TEMP_COOL_DELTA as TEMP_COOL_DELTA,
    AFFECTION_TEMP_FREEZE as TEMP_FREEZE,
    AFFECTION_TEMP_FREEZE_DELTA as TEMP_FREEZE_DELTA,
    AFFECTION_TEMP_HOT as TEMP_HOT,
    AFFECTION_TEMP_HOT_DELTA as TEMP_HOT_DELTA,
    AFFECTION_TEMP_OVER as TEMP_OVER,
    AFFECTION_TEMP_OVER_DELTA as TEMP_OVER_DELTA,
    AFFECTION_TEMP_READ_EVERY_N as TEMP_READ_EVERY_N,
    AFFECTION_TEMP_SWEET as TEMP_SWEET,
    AFFECTION_TEMP_SWEET_DELTA as TEMP_SWEET_DELTA,
    AFFECTION_TEMP_WARM as TEMP_WARM,
    AFFECTION_TEMP_WARM_DELTA as TEMP_WARM_DELTA,
    AFFECTION_TICK_INTERVAL as TICK_INTERVAL,
    AFFECTION_WAKE_DISPLAY_WINDOW as WAKE_DISPLAY_WINDOW,
)
DB_PATH = AFFECTION_DB_PATH
_lock = asyncio.Lock()
from logger import logger

# In-memory state

_mood: float = 0.0
_tick_counter: int = 0
_last_temp: float | None = None
_last_message_time: float = 0.0
_is_sleeping: bool = False
_wake_time: float = -9999.0   # monotonic timestamp of last wake-up

# "Shocked" overlay — global, RAM-only, auto-expires. Never touches DB.
_shocked_reason: str = ""
_shocked_until: float = -9999.0   # monotonic timestamp; expired once now() passes this

# "Aroused" overlay — global, RAM-only, and independent from numeric mood.
_aroused_level: int = 0
_aroused_source: str = ""
_aroused_until: float = -9999.0
_aroused_cooldown_until: float = -9999.0

# DB

async def _init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS global_mood (
                id   INTEGER PRIMARY KEY CHECK (id = 0),
                mood REAL    NOT NULL DEFAULT 0.0
            )
        """)
        await db.execute("INSERT OR IGNORE INTO global_mood (id, mood) VALUES (0, 0.0)")
        await db.commit()


async def _load() -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT mood FROM global_mood WHERE id = 0") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0.0


async def _save(value: float):
    async with _lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE global_mood SET mood = ? WHERE id = 0",
                (max(-100.0, min(100.0, value)),)
            )
            await db.commit()

# Hardware temperature

def _read_temp() -> float | None:
    # Windows — ACPI thermal zones, no external app needed
    try:
        import wmi
        w = wmi.WMI(namespace="root\\wmi")
        zones = w.MSAcpi_ThermalZoneTemperature()
        if zones:
            return max((z.CurrentTemperature / 10.0) - 273.15 for z in zones)
    except Exception:
        pass
    # Linux fallback
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if temps:
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps and temps[key]:
                    return max(e.current for e in temps[key])
            for entries in temps.values():
                if entries:
                    return max(e.current for e in entries)
    except Exception:
        pass
    return None


def _temp_delta(temp: float | None) -> float:
    if temp is None:
        return 0.0
    if temp < TEMP_FREEZE:
        return TEMP_FREEZE_DELTA
    if temp < TEMP_COOL:
        return TEMP_COOL_DELTA
    if temp < TEMP_SWEET:
        return TEMP_SWEET_DELTA
    if temp < TEMP_WARM:
        return 0.0
    if temp < TEMP_HOT:
        return TEMP_WARM_DELTA
    if temp < TEMP_OVER:
        return TEMP_HOT_DELTA
    return TEMP_OVER_DELTA

# Public API

async def initialize():
    global _mood
    await _init_db()
    _mood = await _load()


def get() -> float:
    return _mood


def label(nsfw: bool = False, bond_value: float = 0.0) -> tuple[str, str]:
    if is_shocked():
        return "Sốc", f"Hoshino đang thực sự sốc — {_shocked_reason}"
    if nsfw and is_aroused() and bond_value >= 75.0:
        lvl = aroused_state()["level"]
        if lvl >= 2:
            return "nứng", "Hoshino đang cực kỳ nứng và thèm khát thân mật (BCần Bond ≥50 để ***)."
        elif lvl == 1:
            return "hơi nứng", "Hoshino đang rạo rực, nứng nhẹ trong người (Cần Bond ≥50 để ***)."
    for lo, hi, lbl, desc in MOOD_LEVELS:
        if lo <= _mood < hi:
            return lbl, desc
    return "bình thản", "Hoshino đang ở trạng thái thong dong thường ngày."


def is_sleeping() -> bool:
    return _is_sleeping


def nudge(delta: float):
    global _mood, _last_message_time, _is_sleeping, _wake_time
    import time
    now = time.monotonic()
    if _is_sleeping:
        _wake_time = now
    _mood = max(-100.0, min(100.0, _mood + delta))
    _last_message_time = now
    _is_sleeping = False


def wake():
    """Reset idle timer without changing mood."""
    global _last_message_time, _is_sleeping, _wake_time
    import time
    now = time.monotonic()
    if _is_sleeping:
        _wake_time = now
    _last_message_time = now
    _is_sleeping = False


def just_woke(window: float = WAKE_DISPLAY_WINDOW) -> bool:
    """True for WAKE_DISPLAY_WINDOW seconds after waking from sleep."""
    import time
    return (time.monotonic() - _wake_time) < window


def trigger_shocked(reason: str, duration: float = SHOCKED_DURATION_SECONDS):
    """
    Flip Hoshino into a temporary "shocked" overlay state. Global (not per-user),
    RAM-only — never persisted to DB. Overrides label()/desc for `duration`
    seconds, then auto-expires on its own (lazy check, no background task needed).
    Requires a non-empty reason; silently no-ops without one.
    """
    global _shocked_reason, _shocked_until
    import time
    reason = (reason or "").strip()
    if not reason:
        logger.warning("[mood] trigger_shocked() called with empty reason, ignoring")
        return
    _shocked_reason = reason
    _shocked_until = time.monotonic() + duration
    logger.debug("[mood] shocked triggered: reason=%r duration=%.0fs", reason, duration)


def is_shocked() -> bool:
    """True while the shocked overlay is active (lazily expires past its window)."""
    import time
    return time.monotonic() < _shocked_until


def shocked_reason() -> str:
    """Current shocked reason, or "" if not currently shocked."""
    return _shocked_reason if is_shocked() else ""


def aroused_state() -> dict:
    """Return the current aroused overlay without exposing expired state."""
    import time
    now = time.monotonic()
    if now >= _aroused_until:
        return {"level": 0, "source": "", "remaining": 0.0}
    return {
        "level": _aroused_level,
        "source": _aroused_source,
        "remaining": max(0.0, _aroused_until - now),
    }


def is_aroused() -> bool:
    return aroused_state()["level"] > 0


def trigger_aroused(level: int = 2, source: str = "model") -> bool:
    """Activate the global aroused overlay; returns False for invalid input."""
    global _aroused_level, _aroused_source, _aroused_until, _aroused_cooldown_until
    import time
    now = time.monotonic()
    level = int(level)
    if level not in (1, 2):
        return False
    source = "random" if source == "random" else "model"
    if source == "random" and now < _aroused_cooldown_until:
        return False
    duration = AROUSED_RANDOM_DURATION_SECONDS if source == "random" else AROUSED_MODEL_DURATION_SECONDS
    _aroused_level = level
    _aroused_source = source
    _aroused_until = now + max(0.0, duration)
    if source == "random":
        _aroused_cooldown_until = now + max(0.0, AROUSED_COOLDOWN_SECONDS)
    logger.debug("[mood] aroused level=%s source=%s duration=%.0fs", level, source, duration)
    return True


def maybe_trigger_random_aroused() -> int:
    """Rarely activate the overlay while the global mood is in the happy bucket."""
    import time
    if _mood < 60 or is_aroused() or time.monotonic() < _aroused_cooldown_until:
        return 0
    roll = random.random()
    if roll < AROUSED_RANDOM_LEVEL2_CHANCE:
        return 2 if trigger_aroused(2, source="random") else 0
    if roll < AROUSED_RANDOM_LEVEL2_CHANCE + AROUSED_RANDOM_LEVEL1_CHANCE:
        return 1 if trigger_aroused(1, source="random") else 0
    return 0

# Background tick loop

async def tick_loop():
    """Run as asyncio.create_task(). Never raises."""
    global _mood, _tick_counter, _last_temp, _is_sleeping
    import time

    while True:
        await asyncio.sleep(TICK_INTERVAL)
        _tick_counter += 1

        try:
            idle_secs = time.monotonic() - _last_message_time

            if idle_secs >= SLEEP_AFTER:
                _is_sleeping = True
                regen = SLEEP_REGEN_PER_TICK if _mood < HAPPY_CAP else 0.0
            else:
                _is_sleeping = False
                regen = 0.0

            drift = random.gauss(0, DRIFT_STD)

            if _mood > 0:
                decay = -DECAY_PER_TICK
            elif _mood < 0:
                decay = DECAY_PER_TICK
            else:
                decay = 0.0

            if _tick_counter % TEMP_READ_EVERY_N == 0:
                _last_temp = await asyncio.get_running_loop().run_in_executor(None, _read_temp)
            temp_d = _temp_delta(_last_temp)

            _mood = max(-100.0, min(100.0, _mood + regen + drift + decay + temp_d))

            if _tick_counter % DB_FLUSH_EVERY_N == 0:
                await _save(_mood)

        except Exception as e:
            logger.error("[mood] tick_loop error: %s", e)


async def shutdown():
    """Persist the current mood before the bot exits."""
    await _save(_mood)