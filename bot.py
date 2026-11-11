import asyncio
import os
import signal
import sys
import threading

import config
from client import bot
from control_server import start_control_server
from logger import logger
from mc_console import console_command_listener
from status_notifier import notify_offline, notify_restart
import events  # noqa: F401

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


async def main():
    logger.info("=== Khởi động Hoshino Bot === LOG_LEVEL=%s LOG_DIR=%s", config.LOG_LEVEL, os.path.abspath(config.LOG_DIR))
    loop = asyncio.get_running_loop()

    # Ctrl+C/SIGTERM hoặc HTTP control -> đặt event để kịp gửi thông báo trước khi đóng bot.
    stop_event = asyncio.Event()
    shutdown_state = {"restart": False}

    def _request_action(action: str):
        shutdown_state["restart"] = action == "restart"
        try:
            loop.call_soon_threadsafe(stop_event.set)
        except RuntimeError:
            stop_event.set()

    def _request_stop_signal(*_args):
        _request_action("stop")

    try:
        signal.signal(signal.SIGINT, _request_stop_signal)
        signal.signal(signal.SIGTERM, _request_stop_signal)
    except (AttributeError, OSError, ValueError):
        logger.debug("Không cài được signal handler dừng bot", exc_info=True)

    control_runner = None
    try:
        control_runner = await start_control_server(_request_action)
    except Exception:
        logger.exception("Không mở được control server ở port %s", config.CONTROL_PORT)

    threading.Thread(target=console_command_listener, args=(loop,), daemon=True).start()

    async with bot:
        bot_task = asyncio.create_task(bot.start(config.DISCORD_TOKEN))
        stop_task = asyncio.create_task(stop_event.wait())
        done, _pending = await asyncio.wait(
            {bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done and not bot_task.done():
            if shutdown_state["restart"]:
                await notify_restart()
            else:
                await notify_offline()
            await bot.close()
        elif bot_task.done() and not stop_task.done():
            stop_task.cancel()

        await bot_task

    if control_runner is not None:
        try:
            await control_runner.cleanup()
        except Exception:
            logger.exception("Không đóng được control server")

    if shutdown_state["restart"]:
        logger.info("Đang khởi động lại bot...")
        os.execv(
            sys.executable,
            [sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
        )


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        print("!!! Chưa có DISCORD_TOKEN. Tạo file .env (xem .env.example) rồi điền vào trước khi chạy nhé.")
    else:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            logger.info("Nhận tín hiệu dừng (Ctrl+C hoặc lệnh stop).")
        except Exception:
            logger.exception("Bot crash ngoài ý muốn, thoát chương trình.")
            raise
        finally:
            logger.info("=== Hoshino Bot đã dừng ===")
