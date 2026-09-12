from datetime import datetime
import json

import discord

import config
from attachments import process_attachments
from client import bot, db
from personalization import project_personalization_guide
from prompts import get_hoshino_system_prompt
from affection import affection


def build_context_info(channel: discord.abc.Messageable, current_author) -> str:
    now_str = datetime.now(config.GMT7).strftime("%H:%M ngày %d/%m/%Y")

    guild = getattr(channel, "guild", None)
    guild_name = guild.name if guild else "Tin nhắn riêng (DM)"

    channel_name = getattr(channel, "name", None)
    channel_display = f"#{channel_name}" if channel_name else "DM"

    return (
        "[Thông tin ngữ cảnh hiện tại]\n"
        f"- Thời gian hiện tại: {now_str} (giờ Việt Nam, GMT+7)\n"
        f"- Server: {guild_name}\n"
        f"- Kênh: {channel_display}\n"
        + (f"- Người gửi là bot: true\n" if current_author.bot else "")
    )


def _project_daily_summary(summary_row: dict | None, allow_h_preference: bool) -> str:
    if not summary_row:
        return ""
    try:
        summary = json.loads(summary_row.get("summary_json") or "")
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(summary, dict):
        return ""
    if not allow_h_preference:
        summary.pop("h_preferences", None)
    summary.pop("has_highlight", None)
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


async def build_messages_from_channel(
    channel: discord.abc.Messageable, current_user_text: str, current_author, image_parts=None,
    message_id=None, enable_nsfw=False, affection_context=None, reply_context=None,
):
    collected = []
    async for m in channel.history(limit=config.HISTORY_SCAN_LIMIT):
        if m.author.id == bot.user.id and config.CLEAR_MARKER in m.content:
            break
        if m.id != message_id:
            collected.append(m)
        if len(collected) >= config.HISTORY_LIMIT:
            break

    collected.reverse()

    user_memory = await db.get_memory(current_author.id)
    daily_rows = await db.get_daily_summary_context(current_author.id, keep_latest=3)
    daily_summary = "\n".join(
        projected for row in daily_rows
        if (projected := _project_daily_summary(row, allow_h_preference=enable_nsfw))
    )
    personalization = project_personalization_guide(
        await db.get_personalization(current_author.id),
        allow_h_preference=enable_nsfw,
    )
    system_content = get_hoshino_system_prompt(nsfw=enable_nsfw)
    if user_memory:
        system_content += f"\n\n[Ghi nhớ về {current_author.display_name}]:\n{user_memory}"
    if daily_summary:
        system_content += f"\n\n[Tóm tắt bộ nhớ gần đây của {current_author.display_name}]:\n{daily_summary}"
    if personalization:
        system_content += (
            f"\n\n[Hướng dẫn cá nhân hóa cách trả lời {current_author.display_name}]:\n"
            f"{personalization}"
        )
    if affection_context:
        system_content += f"\n\n{affection.build_prompt_block(affection_context, nsfw=enable_nsfw)}"
    if reply_context:
        system_content += f"\n\n(Replying to {reply_context})"
    # Inject lust bar for NSFW channels (only when lust > 0)
    if enable_nsfw and affection_context:
        lust_value = affection_context.get("lust_value", 0.0)
        if lust_value > 0:
            aroused_level = affection_context.get("aroused_level", 0)
            aroused_emoji = await affection.get_aroused_emoji(current_author.id)
            system_content += f"\n\n[Lust: {lust_value:.1f}%]"
    system_content += f"\n\n{build_context_info(channel, current_author)}"

    msgs = [{"role": "system", "content": system_content}]

    for m in collected:
        if m.author.id == bot.user.id:
            if config.CLEAR_MARKER in m.content:
                continue
            role = "assistant"
            author_prefix = ""
        else:
            if m.author.bot and (not current_author.bot or m.author.id != current_author.id):
                continue
            role = "user"
            author_prefix = f"{m.author.display_name}: "

        attachment_text, attachment_images = await process_attachments(m)
        text_content = m.clean_content.strip()
        if attachment_text:
            text_content = f"{text_content}\n\n{attachment_text}".strip()
        if not text_content and not attachment_images:
            continue

        text_content = f"{author_prefix}{text_content}" if text_content else author_prefix.rstrip()
        if attachment_images:
            content = [{"type": "text", "text": text_content}] + attachment_images
        else:
            content = text_content

        msgs.append({"role": role, "content": content})

    text_part = f"{current_author.display_name}: {current_user_text}"
    if image_parts:
        msgs.append({"role": "user", "content": [{"type": "text", "text": text_part}] + image_parts})
    else:
        msgs.append({"role": "user", "content": text_part})
    return msgs
