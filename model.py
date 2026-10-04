import json
import re
from types import SimpleNamespace

import discord

import config
from client import client_ai, db
from context import build_messages_from_channel
from formatting import cut_partial_tag, scan_thought, split_message
from logger import logger
from tools import TOOLS_SCHEMA, execute_tool_call
from affection import affection

IGNORE_REPLY = "<!-- ignore -->"
_CONTROL_TAG_RE = re.compile(
    r"<mood>.*?</mood>|<shocked>.*?</shocked>|<aroused\s*/>|"
    r"<lust_change\s+amount\s*=\s*['\"][+-]?\d+(?:\.\d+)?['\"]\s*/>|<lust\s*/>|"
    r"<bond_change\b[^>]*?/\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CONTROL_TAG_OPEN_RE = re.compile(r"<(?:mood|shocked|aroused|lust_change|lust|bond_change)\b", re.IGNORECASE)
_CONTROL_TAG_PREFIXES = ("<mood", "<shocked", "<aroused", "<lust_change", "<lust", "<bond_change")


class _ControlTagFilter:
    """Remove affection control tags while preserving streaming output."""

    def __init__(self):
        self._pending = ""

    def feed(self, text: str) -> str:
        self._pending += text or ""
        visible = []
        cursor = 0

        while cursor < len(self._pending):
            opening = _CONTROL_TAG_OPEN_RE.search(self._pending, cursor)
            if not opening:
                safe_end = len(self._pending)
                for index in range(cursor, len(self._pending)):
                    suffix = self._pending[index:].lower()
                    if any(prefix.startswith(suffix) for prefix in _CONTROL_TAG_PREFIXES):
                        safe_end = index
                        break
                visible.append(self._pending[cursor:safe_end])
                self._pending = self._pending[safe_end:]
                return "".join(visible)

            visible.append(self._pending[cursor:opening.start()])
            closing = _CONTROL_TAG_RE.match(self._pending, opening.start())
            if not closing:
                self._pending = self._pending[opening.start():]
                return "".join(visible)
            cursor = closing.end()

        self._pending = ""
        return "".join(visible)

    def finish(self) -> str:
        text = self._pending
        self._pending = ""
        return _CONTROL_TAG_RE.sub("", text)


def split_reasoning(raw_content: str, existing_reasoning: str | None = None):
    """Tách phần reasoning còn sót trong content (mọi kiểu thẻ trong
    config.THOUGHT_TAG_PAIRS) ra khỏi content, gộp với reasoning đã buffer
    được từ stream (existing_reasoning, tức delta.reasoning_content cộng dồn)."""
    content, thoughts = scan_thought(raw_content or "")

    if not thoughts:
        return content.strip(), existing_reasoning

    extracted = "\n".join(thoughts)
    reasoning = f"{existing_reasoning}\n{extracted}" if existing_reasoning else extracted
    return content.strip(), reasoning


class _ThoughtFilter:
    """Giữ mọi khối suy luận (<think>, <|channel>thought, <thought>) khỏi nội dung
    đang stream. Nhận TOÀN BỘ content tích luỹ từ đầu và chỉ trả về phần hội thoại
    mới xuất hiện, nên không thể lọt khối suy luận ra Discord dù thẻ bị stream
    tách vụn qua nhiều delta."""

    def __init__(self):
        self._emitted = 0
        self._text = ""

    def feed(self, full_text: str) -> str:
        self._text = full_text
        visible = cut_partial_tag(scan_thought(full_text)[0])
        if len(visible) <= self._emitted:
            return ""
        chunk = visible[self._emitted:]
        self._emitted = len(visible)
        return chunk

    def finish(self) -> str:
        return self.feed(self._text)


async def _safe_send(channel, content, reply_message=None, on_first_send=None):
    """Gửi message với retry logic khi gặp lỗi message_reference không hợp lệ.
    
    Args:
        channel: discord.abc.Messageable - kênh gửi tin nhắn
        content: str - nội dung tin nhắn
        reply_message: discord.Message | None - tin nhắn để reply (có thể None)
        on_first_send: callable | None - callback khi gửi tin nhắn đầu tiên
        
    Returns:
        bool - True nếu gửi thành công, False nếu thất bại
    """
    if on_first_send is not None:
        on_first_send()
        on_first_send = None
    
    if reply_message is not None:
        try:
            await reply_message.reply(content)
            return True
        except discord.errors.HTTPException as e:
            if e.code == 50035:  # Invalid Form Body - Unknown message
                logger.warning(
                    "Không thể reply vì message_reference không hợp lệ (code 50035), "
                    "sẽ gửi tin nhắn thường thay thế. Error: %s", e
                )
                # Retry without message reference
                try:
                    await channel.send(content)
                    # Reset reply_message sau khi retry thành công
                    return True
                except Exception as e2:
                    logger.error("Lỗi khi gửi tin nhắn thường: %s", e2)
                    return False
            else:
                logger.error("Lỗi HTTPException khi reply: %s", e)
                return False
    else:
        try:
            await channel.send(content)
            return True
        except Exception as e:
            logger.error("Lỗi khi gửi tin nhắn thường: %s", e)
            return False


async def _flush_lines(channel, buf: str, send_state: dict) -> tuple[str, bool]:
    """Gửi từng dòng đã hoàn chỉnh (kết thúc bằng \\n) trong buf tới channel.
    Trả về (phần dư chưa xuống dòng, đã gửi được dòng nào chưa)."""
    sent = False
    while "\n" in buf:
        line, buf = buf.split("\n", 1)
        if line.strip() and line.strip() != IGNORE_REPLY:
            for part in split_message(line, 1900):
                if send_state["on_first_send"] is not None:
                    send_state["on_first_send"]()
                    send_state["on_first_send"] = None
                if send_state["reply_message"] is not None:
                    if not await _safe_send(channel, part, send_state["reply_message"], send_state["on_first_send"]):
                        send_state["reply_message"] = None
                    else:
                        send_state["reply_message"] = None  # Reset sau khi retry thành công
                else:
                    await channel.send(part)
            sent = True
    return buf, sent


async def call_model(
    channel: discord.abc.Messageable,
    user_text: str,
    author,
    image_parts=None,
    message_id=None,
    stream_reply: bool = False,
    nsfw: bool = False,
    reply_message=None,
    on_first_send=None,
    affection_context=None,
    reply_context=None,
):
    """
    stream_reply=False (mặc định): hành vi y hệt bản cũ — trả về (reply, messages)
    đầy đủ, KHÔNG tự gửi gì lên channel. Giữ nguyên để không phá các chỗ khác
    đang gọi call_model mà chưa cập nhật theo API mới.

    stream_reply=True: nội dung được stream từ API, mỗi khi gặp ký tự xuống dòng
    sẽ gửi ngay 1 tin nhắn Discord. Nếu model yêu cầu tool call, KHÔNG stream gì
    cho vòng đó — đợi nhận đủ full tool call rồi mới xử lý, đúng yêu cầu
    tool-calling. Phần reasoning (từ delta.reasoning_content HOẶC từ mọi kiểu
    thẻ suy luận lồng trong content: <think>, <|channel>thought, <thought>)
    không bao giờ bị gửi ra Discord — được buffer lại và save vào
    db.save_reasoning đúng 1 lần sau khi stream xong.
    """
    messages = await build_messages_from_channel(
        channel, user_text, author, image_parts=image_parts, message_id=message_id,
        enable_nsfw=nsfw, affection_context=affection_context, reply_context=reply_context,
    )
    logger.info(
        "call_model bắt đầu: author=%s (%s) channel=%s len(user_text)=%d ảnh=%d",
        author, author.id, channel.id, len(user_text), len(image_parts or []),
    )
    typing_manager = channel.typing()
    await typing_manager.__aenter__()
    _typing_stopped = False

    async def _stop_typing():
        """Tắt typing indicator. Idempotent — gọi bao nhiêu lần cũng chỉ tắt 1 lần."""
        nonlocal _typing_stopped
        if not _typing_stopped:
            _typing_stopped = True
            try:
                await typing_manager.__aexit__(None, None, None)
            except Exception:
                logger.exception("Lỗi khi tắt typing indicator author=%s channel=%s", author.id, channel.id)

    try:
        use_tools = True
        max_output_tokens = config.MAX_OUTPUT_TOKENS_DEFAULT
        #log prompt
        for iteration in range(config.MAX_TOOL_ITER):
            create_kwargs = dict(
                model=config.MODEL_NAME,
                messages=messages,
                temperature=0.7,
                stream=True,
                max_tokens=max_output_tokens,
            )
            if use_tools:
                create_kwargs.update(tools=TOOLS_SCHEMA, tool_choice="auto", reasoning_effort="minimal")

            try:
                stream = await client_ai.chat.completions.create(**create_kwargs)
            except Exception as e:
                if use_tools:
                    logger.warning("Backend không nhận tool calling (%s), thử lại không kèm tools.", e)
                    use_tools = False
                    continue
                logger.exception("call_model lỗi khi gọi API cho author=%s channel=%s", author.id, channel.id)
                raise e

            content_buf = ""
            reasoning_buf = ""  # buffer riêng cho delta.reasoning_content, chỉ save 1 lần ở cuối
            tool_call_chunks: dict[int, dict] = {}
            line_buf = ""
            sent_any = False
            send_state = {"reply_message": reply_message, "on_first_send": on_first_send}
            control_filter = _ControlTagFilter()
            thought_filter = _ThoughtFilter()

            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice0 = chunk.choices[0]
                    delta = choice0.delta

                    # buffer reasoning_content trả riêng field (không phải lồng trong <think>)
                    delta_reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if delta_reasoning:
                        reasoning_buf += delta_reasoning

                    delta_tool_calls = getattr(delta, "tool_calls", None)
                    if delta_tool_calls:
                        for tc_delta in delta_tool_calls:
                            idx = getattr(tc_delta, "index", 0) or 0
                            entry = tool_call_chunks.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                            if getattr(tc_delta, "id", None):
                                entry["id"] = tc_delta.id
                            fn = getattr(tc_delta, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    entry["name"] = fn.name
                                if getattr(fn, "arguments", None):
                                    entry["arguments"] += fn.arguments

                    delta_content = delta.content or ""
                    if delta_content:
                        content_buf += delta_content

                        # Content vẫn được stream nếu cùng lượt có tool call. Tool call
                        # chỉ cần đợi đủ arguments; text thì gửi theo pipeline bình thường.
                        if stream_reply:
                            visible = thought_filter.feed(content_buf)
                            if visible:
                                line_buf += control_filter.feed(visible)
                                line_buf, did_send = await _flush_lines(channel, line_buf, send_state)
                                sent_any = sent_any or did_send
            except Exception:
                logger.exception("Lỗi khi đọc stream cho author=%s channel=%s", author.id, channel.id)
                raise

            # Gộp reasoning_buf (đã cộng dồn suốt stream) với mọi khối suy luận
            # còn sót lại trong content_buf (phòng trường hợp stream_reply=False),
            # rồi save DB đúng 1 lần.
            clean_content, reasoning = split_reasoning(content_buf, reasoning_buf or None)
            if reasoning:
                await db.save_reasoning(author.id, channel.id, reasoning)
                logger.debug("Đã lưu reasoning cho channel=%s (%d ký tự)", channel.id, len(reasoning))

            if not tool_call_chunks:
                # Không có tool call -> đây là câu trả lời cuối cùng của vòng lặp.
                final_reply = affection.parse_and_apply_mood_tag(clean_content, nsfw=nsfw).strip() or "..."
                final_reply = affection.parse_and_apply_bond_tag(final_reply, author.id).strip() or "..."
            
                # Parse lust_change tags
                lust_change, lust_reset = affection.parse_lust_change_tag(final_reply)
                if lust_change != 0.0:
                    if lust_change > 0:
                        await affection.increment_lust(author.id, lust_change)
                        logger.info("[lust] Incremented lust for user=%s by %+g", author.id, lust_change)
                    else:
                        await affection.decrement_lust(author.id, abs(lust_change))
                        logger.info("[lust] Decremented lust for user=%s by %+g", author.id, abs(lust_change))
            
                if lust_reset:
                    await affection.reset_lust(author.id)
                    logger.info("[lust] Reset lust for user=%s (climax)", author.id)

                final_reply = affection.strip_lust_tags(final_reply).strip() or "..."

                # Ngừng "đang gõ" TRƯỚC khi gửi tin nhắn phản hồi cuối cùng, không phải sau.
                await _stop_typing()

                if stream_reply:
                    line_buf += thought_filter.finish()
                    line_buf += control_filter.finish()
                    # Gửi nốt phần dư cuối (dòng không có \n kết thúc).
                    if line_buf.strip() and line_buf.strip() != IGNORE_REPLY:
                        for part in split_message(line_buf, 1900):
                            if send_state["reply_message"] is not None:
                                if not await _safe_send(channel, part, send_state["reply_message"], send_state["on_first_send"]):
                                    send_state["reply_message"] = None
                                else:
                                    send_state["reply_message"] = None  # Reset sau khi retry thành công
                            else:
                                await channel.send(part)
                        sent_any = True
                    # Fallback: nếu suốt quá trình stream chưa gửi được gì (vd model chỉ
                    # trả <think> không đóng thẻ, hoặc nội dung rỗng) thì gửi nguyên câu
                    # trả lời cuối để Sensei không bị bỏ rơi.
                    if not sent_any and final_reply != IGNORE_REPLY:
                        for part in split_message(final_reply, 1900):
                            if send_state["on_first_send"] is not None:
                                send_state["on_first_send"]()
                                send_state["on_first_send"] = None
                            if send_state["reply_message"] is not None:
                                if not await _safe_send(channel, part, send_state["reply_message"], send_state["on_first_send"]):
                                    send_state["reply_message"] = None
                                else:
                                    send_state["reply_message"] = None  # Reset sau khi retry thành công
                            else:
                                await channel.send(part)
                logger.info(
                    "call_model xong sau %d vòng lặp, len(reply)=%d",
                    iteration + 1, len(final_reply),
                )
                return final_reply, messages

            if stream_reply:
                line_buf += thought_filter.finish()
                line_buf += control_filter.finish()
                if line_buf.strip() and line_buf.strip() != IGNORE_REPLY:
                    for part in split_message(line_buf, 1900):
                        if send_state["on_first_send"] is not None:
                            send_state["on_first_send"]()
                            send_state["on_first_send"] = None
                        if send_state["reply_message"] is not None:
                            if not await _safe_send(channel, part, send_state["reply_message"], send_state["on_first_send"]):
                                send_state["reply_message"] = None
                            else:
                                send_state["reply_message"] = None  # Reset sau khi retry thành công
                        else:
                            await channel.send(part)
                    sent_any = True

            tool_calls = [
                SimpleNamespace(
                    id=entry["id"],
                    function=SimpleNamespace(name=entry["name"], arguments=entry["arguments"]),
                )
                for _, entry in sorted(tool_call_chunks.items())
            ]

            logger.info(
                "Model yêu cầu %d tool call(s) ở vòng %d: %s",
                len(tool_calls), iteration + 1, [tc.function.name for tc in tool_calls],
            )

            messages.append(
                {
                    "role": "assistant",
                    "content": clean_content,
                    **({"reasoning_content": reasoning} if reasoning else {}),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if tc.function.name == "increase_output_tokens":
                    try:
                        requested_tokens = int(args.get("max_tokens") or 0)
                    except (TypeError, ValueError):
                        requested_tokens = 0
                    if requested_tokens > max_output_tokens:
                        max_output_tokens = min(requested_tokens, config.MAX_OUTPUT_TOKENS_LIMIT)
                        logger.info("Model chủ động tăng max_tokens lên %d", max_output_tokens)
                    else:
                        logger.info(
                            "Model xin max_tokens=%d nhưng đã ở mức cao nhất (%d), giữ nguyên",
                            requested_tokens, max_output_tokens,
                        )
                guild_id = channel.guild.id if getattr(channel, "guild", None) else None
                result = await execute_tool_call(tc.function.name, args, author.id, channel_id=channel.id, guild_id=guild_id)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        logger.warning(
            "call_model vượt quá MAX_TOOL_ITER=%d, ép trả lời fallback (author=%s channel=%s)",
            config.MAX_TOOL_ITER, author.id, channel.id,
        )
        fallback_reply = "Uhe~... hơi rối quá, để em nghỉ xíu đã ha Sensei, hỏi lại em sau nhé."
        # Ngừng "đang gõ" TRƯỚC khi gửi tin nhắn fallback cuối cùng, không phải sau.
        await _stop_typing()
        if stream_reply:
            for part in split_message(fallback_reply, 1900):
                if reply_message is not None:
                    if not await _safe_send(channel, part, reply_message, on_first_send):
                        reply_message = None
                else:
                    await channel.send(part)
        return fallback_reply, messages
    finally:
        # Đảm bảo typing luôn được tắt, dù thành công, lỗi, hay return sớm ở đâu.
        await _stop_typing()
