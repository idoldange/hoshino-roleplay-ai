import asyncio
import time
from datetime import datetime

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

        cmd = line.strip().lower()
        if not cmd:
            continue

        if cmd in ["stop", "end"]:
            asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            break


async def _shutdown():
    try:
        await notify_offline()
        await affection.shutdown()
        await bot.close()
    except Exception:
        pass
