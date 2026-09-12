import asyncio
import logging
import time
from datetime import datetime

from client import bot
from logger import logger
from affection import affection

_START_TS = time.time()


def mc_log(message: str, level: str = "INFO", thread: str = "Server thread"):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{thread}/{level}]: {message}", flush=True)
    logger.log(getattr(logging, level.upper(), logging.INFO), f"[{thread}] {message}")


def print_fake_mc_startup_logs():
    mc_log(f"Time elapsed: {int((time.time() - _START_TS) * 1000)} ms")


def print_fake_mc_done_log():
    elapsed = time.time() - _START_TS
    print(f"[{datetime.now().strftime('%H:%M:%S')} INFO]: Listening on /[0:0:0:0:0:0:0:0]:1234")
    print(f"[{datetime.now().strftime('%H:%M:%S')} INFO]: Done ({elapsed}s)!")


def print_fake_mc_stop_logs():
    mc_log("Stopping the server")
    mc_log("Stopping server")
    mc_log("Saving players")
    mc_log("Saving worlds")
    mc_log('ThreadedAnvilChunkStorage (world): All chunks are saved')


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
            print_fake_mc_stop_logs()
            asyncio.run_coroutine_threadsafe(_shutdown(), loop)
            break
        elif cmd == "help":
            mc_log('Unknown command. Type "help" for help.')
        else:
            mc_log(f'Unknown command "{cmd}". Type "help" for help.')


async def _shutdown():
    try:
        await affection.shutdown()
        await bot.close()
    except Exception:
        pass
