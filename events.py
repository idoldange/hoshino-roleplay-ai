import asyncio
from collections import deque

import discord

import config
from attachments import process_attachments
from client import bot, db
from commands import handle_command
from formatting import split_message
from logger import logger
from mc_console import mc_log, print_fake_mc_done_log
from memory_reflection import reflect_and_update_memory
from daily_memory import daily_summary_loop
from model import IGNORE_REPLY, call_model
from affection import affection


_channel_requests = {}
_affection_started = False
_daily_summary_stop = None
_daily_summary_task = None


async def _get_reply_context(message: discord.Message) -> str | None:
    reference = message.reference
    if reference is None or reference.message_id is None:
        return None

    referenced = reference.resolved
    if not isinstance(referenced, discord.Message):
        try:
            referenced = await message.channel.fetch_message(reference.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.debug("Không lấy được message được reply: %s", reference.message_id)
            return None

    content = referenced.content.strip()
    if not content:
        content = "(tin nhắn không có nội dung văn bản)"
    return f"{referenced.author.display_name}: {content}"


async def _run_request(channel, request, state):
    state["request"] = request
    request_author = request["author"]
    message = request["message"]
    user_text = request["user_text"]
    state["started"] = False

    def mark_started():
        state["started"] = True

    try:
        if True: #async with channel.typing():
            try:
                nsfw = request["nsfw"]
                reply, used_messages = await call_model(
                    channel, user_text, request_author,
                    image_parts=request["image_parts"], message_id=message.id,
                    stream_reply=True, nsfw=nsfw,
                    reply_message=message, on_first_send=mark_started,
                    affection_context=request["affection_context"],
                    reply_context=request["reply_context"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(
                    "Lỗi xử lý tin nhắn từ %s (%s) trong channel=%s",
                    request_author, request_author.id, channel.id,
                )
                reply = f"...lỗi rồi, chắc server sập. ({e})"
                used_messages = None
                mark_started()
                for index, chunk in enumerate(split_message(reply, 1900)):
                    if index == 0:
                        await message.reply(chunk)
                    else:
                        await channel.send(chunk)

        if used_messages is not None and reply != IGNORE_REPLY:
            guild_id = message.guild.id if message.guild else None
            guild_name = message.guild.name if message.guild else None
            channel_name = getattr(channel, "name", None) or "DM"
            await db.add_global_history(
                request_author.id, "user", f"{request_author.display_name}: {user_text}",
                channel_id=channel.id, channel_name=channel_name,
                guild_id=guild_id, guild_name=guild_name, keep=config.GLOBAL_HISTORY_KEEP,
            )
            await db.add_global_history(
                request_author.id, "assistant", reply,
                channel_id=channel.id, channel_name=channel_name,
                guild_id=guild_id, guild_name=guild_name, keep=config.GLOBAL_HISTORY_KEEP,
            )
            messages_with_reply = used_messages + [{"role": "assistant", "content": reply}]
            asyncio.create_task(
                reflect_and_update_memory(
                    channel.id,
                    request_author,
                    messages_with_reply,
                    allow_h_preference=request["nsfw"],
                )
            )
    finally:
        if state.get("active") is asyncio.current_task():
            state["active"] = None
            if state["queue"]:
                next_request = state["queue"].popleft()
                state["request"] = next_request
                state["active"] = asyncio.create_task(
                    _run_request(channel, next_request, state)
                )
            else:
                _channel_requests.pop(channel.id, None)


def _enqueue_request(channel, request):
    state = _channel_requests.setdefault(
        channel.id, {"active": None, "started": False, "request": None, "queue": deque()}
    )
    active = state["active"]
    if active is None:
        state["request"] = request
        state["active"] = asyncio.create_task(_run_request(channel, request, state))
        return

    current_request = state["request"]
    if (
        not state["started"]
        and current_request is not None
        and current_request["author"].id == request["author"].id
    ):
        active.cancel()
        request["user_text"] = f"{current_request['user_text']}\n{request['user_text']}"
        state["request"] = request
        state["active"] = asyncio.create_task(_run_request(channel, request, state))
        return

    state["queue"].append(request)


@bot.event
async def on_ready():
    global _affection_started, _daily_summary_stop, _daily_summary_task
    await db.init()
    if not _affection_started:
        await affection.initialize()
        await affection.start()
        _affection_started = True
    if _daily_summary_task is None or _daily_summary_task.done():
        _daily_summary_stop = asyncio.Event()
        _daily_summary_task = asyncio.create_task(daily_summary_loop(_daily_summary_stop))
        logger.info("daily_summary.task_created task_id=%s", id(_daily_summary_task))
    print_fake_mc_done_log()
    mc_log(f"Đã đăng nhập Discord với tài khoản: {bot.user}", thread="Hoshino")
    logger.info("Bot sẵn sàng: %s (id=%s), đang ở %d server", bot.user, bot.user.id, len(bot.guilds))


@bot.event
async def on_message(message: discord.Message):
    if message.author.id == bot.user.id:
        return
    if message.author.bot and "@hoshino" not in message.content.lower() and "!hoshino" not in message.content.lower() and not bot.user in message.mentions:
        return

    content = message.clean_content.strip()
    is_mention = bot.user in message.mentions
    is_prefix = content.startswith(config.BOT_PREFIX)
    is_dm = message.guild is None
    is_hoshino_in_content = False #"hoshino" in content.lower()

    if is_prefix:
        cmd_text = content[len(config.BOT_PREFIX):].strip()
        handled = await handle_command(message, cmd_text)
        if handled:
            return
        user_text = cmd_text
    elif is_mention:
        user_text = content
        for m in message.mentions:
            user_text = user_text.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        user_text = user_text.strip()
    elif is_dm:
        user_text = content
    elif is_hoshino_in_content:
        user_text = content
    else:
        if not await db.is_auto_channel(message.channel.id):
            return
        user_text = content

    extra_text, image_parts = await process_attachments(message)
    if extra_text:
        user_text = f"{user_text}\n\n{extra_text}".strip()

    if not user_text:
        user_text = "..."

    # DM and explicitly marked Discord NSFW channels use the NSFW prompt path.
    nsfw = message.guild is None or bool(getattr(message.channel, "nsfw", False))
    affection_context = await affection.on_interaction(message.author.id, nsfw=nsfw)
    reply_context = await _get_reply_context(message)

    _enqueue_request(
        message.channel,
        {
            "message": message,
            "author": message.author,
            "user_text": user_text,
            "image_parts": image_parts,
            "nsfw": nsfw,
            "affection_context": affection_context,
            "reply_context": reply_context,
        },
    )
