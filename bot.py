import asyncio
import os
import signal
import threading

import config
from client import bot
from logger import logger
from mc_console import console_command_listener, mc_log, print_fake_mc_startup_logs
from status_notifier import notify_offline
import events  # noqa: F401


async def main():
    logger.info("=== Khởi động Hoshino Bot === LOG_LEVEL=%s LOG_DIR=%s", config.LOG_LEVEL, os.path.abspath(config.LOG_DIR))
    print_fake_mc_startup_logs()
    loop = asyncio.get_running_loop()

    # Ctrl+C/SIGTERM -> đặt event để kịp gửi thông báo tắt trước khi đóng bot.
    stop_event = asyncio.Event()

    def _request_stop(*_args):
        loop.call_soon_threadsafe(stop_event.set)

    try:
        signal.signal(signal.SIGINT, _request_stop)
        signal.signal(signal.SIGTERM, _request_stop)
    except (AttributeError, OSError, ValueError):
        logger.debug("Không cài được signal handler dừng bot", exc_info=True)

    threading.Thread(target=console_command_listener, args=(loop,), daemon=True).start()

    async with bot:
        bot_task = asyncio.create_task(bot.start(config.DISCORD_TOKEN))
        stop_task = asyncio.create_task(stop_event.wait())
        done, _pending = await asyncio.wait(
            {bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done and not bot_task.done():
            await notify_offline()
            await bot.close()
        elif bot_task.done() and not stop_task.done():
            stop_task.cancel()

        await bot_task


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
            mc_log("Server stopped.")
            logger.info("=== Hoshino Bot đã dừng ===")
