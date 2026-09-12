import asyncio
import os
import threading

import config
from client import bot
from logger import logger
from mc_console import console_command_listener, mc_log, print_fake_mc_startup_logs
import events  # noqa: F401


async def main():
    logger.info("=== Khởi động Hoshino Bot === LOG_LEVEL=%s LOG_DIR=%s", config.LOG_LEVEL, os.path.abspath(config.LOG_DIR))
    print_fake_mc_startup_logs()
    loop = asyncio.get_running_loop()
    threading.Thread(target=console_command_listener, args=(loop,), daemon=True).start()

    async with bot:
        await bot.start(config.DISCORD_TOKEN)


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
