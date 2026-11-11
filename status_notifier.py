"""Gửi thông báo bật/tắt bot vào STATUS_CHANNEL_ID."""

import discord

import config
from client import bot
from logger import logger

_offline_sent = False


async def _send(content: str) -> bool:
    channel_id = getattr(config, "STATUS_CHANNEL_ID", 0) or 0
    if not channel_id:
        return True

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            logger.error("STATUS_CHANNEL_ID=%s không tồn tại", channel_id)
            return False
        except discord.Forbidden:
            logger.error("Không có quyền xem kênh STATUS_CHANNEL_ID=%s", channel_id)
            return False
        except Exception:
            logger.exception("Không lấy được kênh STATUS_CHANNEL_ID=%s", channel_id)
            return False

    try:
        await channel.send(content)
        return True
    except Exception:
        logger.exception("Không gửi được thông báo vào channel=%s", channel_id)
        return False


async def notify_online() -> bool:
    return await _send("🟢 Hoshino đã bật.")


async def notify_offline() -> bool:
    global _offline_sent
    if _offline_sent:
        return True
    ok = await _send("🔴 Hoshino đã tắt.")
    if ok:
        _offline_sent = True
    return ok
