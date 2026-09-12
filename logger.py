import logging
import os
from logging.handlers import RotatingFileHandler

import config

os.makedirs(config.LOG_DIR, exist_ok=True)

logger = logging.getLogger("hoshino")
logger.setLevel(config.LOG_LEVEL)

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_file_handler = RotatingFileHandler(
    os.path.join(config.LOG_DIR, "hoshino_bot.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)
logger.addHandler(_file_handler)

if config.LOG_TO_CONSOLE:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_log_formatter)
    logger.addHandler(_console_handler)

for _noisy_logger_name in ("discord", "discord.http", "discord.gateway", "openai", "httpx"):
    _noisy_logger = logging.getLogger(_noisy_logger_name)
    _noisy_logger.setLevel(logging.WARNING)
    _noisy_logger.addHandler(_file_handler)
