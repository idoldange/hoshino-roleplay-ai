import asyncio
import shlex
import time
from datetime import datetime

import discord

import config
from client import bot
from affection import affection
from status_notifier import notify_offline

_START_TS = time.time()


def print_fake_mc_done_log():
    elapsed = time.time() - _START_TS
    print(
        f"[{datetime.now().strftime('%H:%M:%S')} INFO]: Listening on /[0:0:0:0:0:0:0:0]:{config.CONTROL_PORT}",
        flush=True,
    )
    print(f"[{datetime.now().strftime('%H:%M:%S')} INFO]: Done ({elapsed}s)!", flush=True)


def console_command_listener(loop: asyncio.AbstractEventLoop):
    while True:
        try:
            line = input()
        except (EOFError, OSError):
            break

        raw = line.strip()
        cmd = raw.lower()
        if not cmd:
            continue

        if cmd == "status" or cmd.startswith("status "):
            asyncio.run_coroutine_threadsafe(handle_status_command(raw), loop)
            continue

        if cmd in ["stop", "end"]:
            asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            break


_STATUS_MAP = {
    "dnd": discord.Status.dnd,
    "idle": discord.Status.idle,
    "online": discord.Status.online,
    "invisible": discord.Status.invisible,
}
_ACTIVITY_TYPES = {
    "P": discord.ActivityType.playing,
    "L": discord.ActivityType.listening,
    "S": discord.ActivityType.streaming,
    "W": discord.ActivityType.watching,
    "C": discord.ActivityType.competing,
    "T": discord.ActivityType.custom,
}
_STATUS_USAGE = (
    "Usage: STATUS dnd/idle/online/invisible P/L/S/W/C/T \"text\" [url]  "
    "(T = custom text status, the bubble above the avatar)"
)


async def handle_status_command(cmd: str):
    """STATUS dnd/idle/online/invisible P/L/S/W/C/T "text" [url]

    Example: STATUS online T "you have a sandevistan too?"
    """
    try:
        parts = shlex.split(cmd)
        if len(parts) < 4:
            print(_STATUS_USAGE, flush=True)
            return

        status_str = parts[1].lower()
        type_str = parts[2].upper()
        name = parts[3]
        url = parts[4] if len(parts) > 4 else "https://www.youtube.com/@BlueArchive_JP"

        if status_str not in _STATUS_MAP:
            print("Invalid status! Use: dnd, idle, online, invisible", flush=True)
            return
        if type_str not in _ACTIVITY_TYPES:
            print("Invalid activity type! Use: P, L, S, W, C, T", flush=True)
            return

        if type_str == "S":
            activity = discord.Streaming(name=name, url=url)
        elif type_str == "P":
            activity = discord.Game(name=name)
        elif type_str == "T":
            activity = discord.CustomActivity(name=name)
        else:
            activity = discord.Activity(type=_ACTIVITY_TYPES[type_str], name=name)

        await bot.change_presence(status=_STATUS_MAP[status_str], activity=activity)
        print(f"Status updated: {status_str.upper()} - {type_str} - {name!r}", flush=True)
    except Exception as e:
        print(f"Failed to update status: {e}", flush=True)


async def _shutdown():
    try:
        await notify_offline()
        await affection.shutdown()
        await bot.close()
    except Exception:
        pass
