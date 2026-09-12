import asyncio

from client import client_ai, db
from logger import logger
from model import split_reasoning
from personalization import project_personalization_guide, validate_personalization_guide
from prompts import get_memory_reflection_system_prompt
import config


def _render_transcript_for_reflection(messages: list) -> str:
    lines = []
    for m in messages:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if isinstance(content, list):
            text_bits = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content_str = " ".join(t for t in text_bits if t).strip()
            if any(isinstance(c, dict) and c.get("type") == "image_url" for c in content):
                content_str = (content_str + " [kèm ảnh]").strip()
        else:
            content_str = (content or "").strip()

        if not content_str:
            continue
        lines.append(content_str if role == "user" else f"Hoshino: {content_str}")

    return "\n".join(lines).strip()




async def reflect_and_update_memory(channel_id: int, author, messages_with_reply: list, allow_h_preference: bool = False):
    try:
        logger.info("Reflection bắt đầu cho author_id=%s channel_id=%s", author.id, channel_id)
        transcript = _render_transcript_for_reflection(messages_with_reply)
        if not transcript:
            return

        global_rows = await db.get_global_history(author.id, limit=config.GLOBAL_HISTORY_LIMIT)
        global_transcript = _render_transcript_for_reflection(global_rows)

        current_memory = project_personalization_guide(
            await db.get_personalization(author.id),
            allow_h_preference=allow_h_preference,
        )
        user_content = (
            f"[Hướng dẫn cá nhân hóa hiện có về {author.display_name}]:\n{current_memory or '(chưa có gì)'}\n\n"
            f"[Lịch sử trò chuyện dài hạn (gộp mọi kênh/DM)]:\n{global_transcript or '(chưa có)'}\n\n"
            f"[Đoạn hội thoại gần đây]:\n{transcript}"
        )

        logger.info(
            "Reflection gọi model cho author_id=%s (timeout=%ss, max_tokens=%s)",
            author.id, config.REFLECTION_TIMEOUT_SECONDS, config.REFLECTION_MAX_TOKENS,
        )
        resp = await asyncio.wait_for(
            client_ai.chat.completions.create(
                model=config.REFLECTION_MODEL_NAME,
                messages=[
                    {
                        "role": "system",
                        "content": get_memory_reflection_system_prompt(allow_h_preference),
                    },
                    {"role": "user", "content": user_content},
                ],
                temperature=0.5,
                max_tokens=config.REFLECTION_MAX_TOKENS,
            ),
            timeout=config.REFLECTION_TIMEOUT_SECONDS,
        )
        logger.info("Reflection nhận response cho author_id=%s", author.id)
        choice = resp.choices[0].message
        clean_content, _ = split_reasoning(choice.content)
        clean_content = clean_content.strip()

        if not clean_content or clean_content == "-":
            logger.debug("Reflection: không có gì mới cần lưu cho author_id=%s", author.id)
            return

        updated_memory = validate_personalization_guide(
            clean_content,
            allow_h_preference=allow_h_preference,
        )
        if updated_memory is None:
            logger.warning("Reflection: bỏ qua personalization không hợp lệ cho author_id=%s:\n%s", author.id, clean_content)
            return

        await db.set_personalization(author.id, updated_memory)
        logger.info("Reflection: đã áp dụng personalization cho author_id=%s:\n%s", author.id, clean_content)
    except Exception:
        logger.exception("Reflection memory lỗi cho author_id=%s channel_id=%s", author.id, channel_id)
