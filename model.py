import json
import re
from types import SimpleNamespace

import discord
from openai import AsyncOpenAI

import config
from client import bot, client_ai, db
from context import build_messages_from_channel
from formatting import cut_partial_tag, scan_thought, split_message
from logger import logger
from tools import TOOLS_SCHEMA, execute_tool_call
from affection import affection
import endpoint_profiles

IGNORE_REPLY = "<!-- ignore -->"


def _speaker_prefix_re():
    names = {"Takanashi Hoshino", "Hoshino Takanashi", "Hoshino", "Takanashi"}
    user = getattr(bot, "user", None)
    if user is not None:
        for n in (getattr(user, "display_name", None), getattr(user, "name", None)):
            if n:
                names.add(n)
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(
        rf"^[ \t]*[\[(]?[*_~`]{{0,3}}(?:{alt})[*_~`]{{0,3}}[\])]?[ \t]*[:\uff1a][ \t]*",
        re.IGNORECASE | re.MULTILINE,
    )


def strip_speaker_prefix(text: str) -> str:
    """Strip 'Hoshino: ' style prefix at the start of each line of model output
    (model mimics the 'Name: text' format used for history/user messages)."""
    if not text:
        return text
    return _speaker_prefix_re().sub("", text)
_CONTROL_TAG_RE = re.compile(
    r"<mood>.*?</mood>"
    r"|<shocked>.*?</shocked>"
    r"|<bond>.*?</bond>"
    r"|<aroused\s*/>"
    r"|<arousal_change\s+amount\s*=\s*['\"][+-]?\d+(?:\.\d+)?['\"]\s*/>"
    r"|<lust_change\s+amount\s*=\s*['\"][+-]?\d+(?:\.\d+)?['\"]\s*/>|<climax\s*/>|<lust\s*/>"
    r"|<experience_change\s+amount\s*=\s*['\"][+-]?\d+(?:\.\d+)?['\"]\s*/>"
    r"|<bond_change\b[^>]*?/\s*>"
    r"|<:mood>\s*[+-]?\d+(?:\.\d+)?\s*</mood>"
    r"|</mood>\s*\[[+-]?\d+(?:\.\d+)?\]"
    r"|</mood>\s*[+-]?\d+(?:\.\d+)?\s*>"
    r"|</mood>\s*[+-]?\d+(?:\.\d+)?\s*</mood>"
    r"|<mood\s*/>"
    r"|</:?mood\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_MOOD_RE = re.compile(r"<:?mood\b.*$", re.IGNORECASE | re.DOTALL)
_CONTROL_TAG_OPEN_RE = re.compile(
    r"<(?:mood|shocked|bond|aroused|arousal_change|lust_change|lust|climax|experience_change|bond_change)\b|</?:?mood\b|<:mood\b",
    re.IGNORECASE,
)
_CONTROL_TAG_PREFIXES = (
    "<mood", "<shocked", "<bond", "<aroused", "<arousal_change", "<lust_change", "<lust", "<climax",
    "<experience_change", "<bond_change", "</mood", "</:mood", "<:mood",
)


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
        text = _CONTROL_TAG_RE.sub("", text)
        if _UNCLOSED_MOOD_RE.search(text):
            logger.warning("[tags] bỏ <mood> chưa đóng ở cuối stream")
        return _UNCLOSED_MOOD_RE.sub("", text)


def _is_unsupported_media(error: Exception) -> bool:
    """Lỗi API do backend không nhận loại nội dung (ví dụ ảnh), thường HTTP 415."""
    status = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status is not None:
        try:
            return int(status) == 415
        except (TypeError, ValueError):
            pass
    return "unsupported media type" in str(error).lower()


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
        line = strip_speaker_prefix(line)
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


async def _create_model_stream(create_kwargs: dict, author_id: int, model_info: dict | None):
    profile_id = (model_info or {}).get("profile_id")
    if not profile_id:
        return await client_ai.chat.completions.create(**create_kwargs), None

    auth = await endpoint_profiles.get_profile_auth(author_id, profile_id)
    for attempt in range(auth["key_count"]):
        client = AsyncOpenAI(base_url=auth["base_url"], api_key=auth["api_key"])
        try:
            stream = await client.chat.completions.create(**create_kwargs)
            return stream, client
        except Exception as exc:
            await client.close()
            if (
                not endpoint_profiles.is_retryable_provider_error(exc)
                or attempt + 1 >= auth["key_count"]
            ):
                raise
            auth = await endpoint_profiles.advance_profile_key(author_id, profile_id)
            logger.warning(
                "Endpoint profile rate/temporary error; trying key %d/%d (status=%s)",
                auth["key_index"] + 1, auth["key_count"],
                getattr(exc, "status_code", None),
            )
    raise endpoint_profiles.ProfileStoreError("Không gọi được endpoint profile.")


async def call_model(
    channel: discord.abc.Messageable,
    user_text: str,
    author,
    image_parts=None,
    audio_parts=None,
    video_parts=None,
    message_id=None,
    stream_reply: bool = False,
    nsfw: bool = False,
    reply_message=None,
    on_first_send=None,
    affection_context=None,
    reply_context=None,
    model_name: str | None = None,
    allow_images: bool = True,
    allow_audio: bool = False,
    allow_video: bool = False,
    model_info: dict | None = None,
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
        channel, user_text, author, image_parts=image_parts,
        audio_parts=audio_parts, video_parts=video_parts, message_id=message_id,
        enable_nsfw=nsfw, affection_context=affection_context, reply_context=reply_context,
        allow_images=allow_images, allow_audio=allow_audio, allow_video=allow_video,
    )
    has_media = bool(
        (image_parts and allow_images)
        or (audio_parts and allow_audio)
        or (video_parts and allow_video)
    )
    retried_no_images = False
    # check perm coi dc gui tin nhan ko
    #channel la message.channel
    perm = (
        channel.permissions_for(channel.guild.me)
        if hasattr(channel, "permissions_for") and channel.guild
        else None
    )
    if perm and not perm.send_messages:
        logger.warning("Không có quyền gửi tin nhắn trong channel %s", channel.id)
        return
    
    logger.info(
        "call_model bắt đầu: author=%s (%s) channel=%s len(user_text)=%d ảnh=%d audio=%d video=%d",
        author, author.id, channel.id, len(user_text),
        len(image_parts or []), len(audio_parts or []), len(video_parts or []),
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
        use_tools = bool((model_info or {}).get("tool", True))
        max_output_tokens = config.MAX_OUTPUT_TOKENS_DEFAULT
        call_model._retry_count = 0
        #log prompt
        for iteration in range(config.MAX_TOOL_ITER):
            create_kwargs = dict(
                model=model_name or config.MODEL_NAME,
                messages=messages,
                temperature=0.7,
                stream=True,
                max_tokens=max_output_tokens,
            )
            if use_tools:
                create_kwargs.update(tools=TOOLS_SCHEMA, tool_choice="auto", reasoning_effort="minimal")

            try:
                stream, stream_client = await _create_model_stream(
                    create_kwargs, author.id, model_info
                )
            except Exception as e:
                profile_id = (model_info or {}).get("profile_id")
                if profile_id and (
                    endpoint_profiles.is_retryable_provider_error(e)
                    or getattr(e, "status_code", None) not in {400, 404, 422}
                ):
                    logger.error(
                        "Endpoint profile request failed for author=%s (status=%s)",
                        author.id, getattr(e, "status_code", None),
                    )
                    raise
                if use_tools:
                    logger.warning("Backend không nhận tool calling, thử lại không kèm tools.")
                    use_tools = False
                    continue
                if profile_id:
                    logger.error(
                        "Endpoint profile request failed for author=%s (status=%s)",
                        author.id, getattr(e, "status_code", None),
                    )
                else:
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
            except Exception as exc:
                if (
                    not retried_no_images
                    and has_media
                    and _is_unsupported_media(exc)
                ):
                    retried_no_images = True
                    logger.warning(
                        "Backend không nhận media (ảnh/audio/video), thử lại chỉ văn bản cho author=%s channel=%s",
                        author.id, channel.id,
                    )
                    text_only = await build_messages_from_channel(
                        channel, user_text, author, image_parts=None,
                        audio_parts=None, video_parts=None, message_id=message_id,
                        enable_nsfw=nsfw, affection_context=affection_context,
                        reply_context=reply_context, allow_images=False,
                        allow_audio=False, allow_video=False,
                    )
                    messages = text_only
                    continue
                if (model_info or {}).get("profile_id"):
                    logger.error("Lỗi khi đọc stream endpoint profile cho author=%s", author.id)
                else:
                    logger.exception("Lỗi khi đọc stream cho author=%s channel=%s", author.id, channel.id)
                raise
            finally:
                if stream_client is not None:
                    await stream_client.close()

            # Gộp reasoning_buf (đã cộng dồn suốt stream) với mọi khối suy luận
            # còn sót lại trong content_buf (phòng trường hợp stream_reply=False),
            # rồi save DB đúng 1 lần.
            clean_content, reasoning = split_reasoning(content_buf, reasoning_buf or None)
            if reasoning:
                await db.save_reasoning(author.id, channel.id, reasoning)
                logger.debug("Đã lưu reasoning cho channel=%s (%d ký tự)", channel.id, len(reasoning))

            if not tool_call_chunks:
                # Không có tool call -> đây là câu trả lời cuối cùng của vòng lặp.
                final_reply = affection.parse_and_apply_mood_tag(
                    clean_content,
                    user_id=author.id,
                    scope={"is_dm": nsfw},
                    turn_id=message_id,
                ).strip() or "..."

                # Arousal (transient) + Lust (persistent level)
                lust_tags = affection.parse_lust_tags(final_reply)
                if lust_tags["arousal"]:
                    await affection.add_arousal(author.id, lust_tags["arousal"])
                    logger.info("[arousal] %+g for user=%s", lust_tags["arousal"], author.id)
                if lust_tags["lust"]:
                    level = await affection.add_lust_level(author.id, lust_tags["lust"])
                    logger.info("[lust] level %+d for user=%s -> lv%d", lust_tags["lust"], author.id, level)
                if lust_tags["climax"]:
                    await affection.reset_arousal(author.id)
                    logger.info("[arousal] reset for user=%s (climax)", author.id)
                final_reply = affection.strip_lust_tags(final_reply)

                # Experience tags
                exp_change = affection.parse_experience_change_tag(final_reply)
                if exp_change > 0:
                    await affection.increment_experience(author.id, exp_change)
                    logger.info("[experience] Incremented for user=%s by %+g", author.id, exp_change)
                elif exp_change < 0:
                    await affection.decrement_experience(author.id, abs(exp_change))
                    logger.info("[experience] Decremented for user=%s by %+g", author.id, abs(exp_change))
                final_reply = strip_speaker_prefix(affection.strip_experience_tags(final_reply)).strip() or "..."

                # Auto-retry nếu chỉ có control tags (kết quả chỉ là "...")
                if final_reply == "..." and max_output_tokens < config.MAX_OUTPUT_TOKENS_LIMIT:
                    retry_count = getattr(call_model, "_retry_count", 0)
                    if retry_count < 2:
                        call_model._retry_count = retry_count + 1
                        old_max = max_output_tokens
                        max_output_tokens = min(max_output_tokens * 2, config.MAX_OUTPUT_TOKENS_LIMIT)
                        logger.warning(
                            "Model chỉ trả control tags (reply='...'), retry %d/2 với max_tokens=%d (từ %d)",
                            call_model._retry_count, max_output_tokens, old_max
                        )
                        # Thêm hint cho model biết cần output dialogue text
                        messages.append({
                            "role": "system",
                            "content": "(Hệ thống: Phản hồi trước chỉ chứa thẻ điều khiển nội bộ. Hãy viết thêm dialogue/hành động thực sự cho Sensei thấy.)"
                        })
                        await _stop_typing()
                        continue  # retry vòng lặp với max_tokens lớn hơn
                    else:
                        call_model._retry_count = 0

                # Ngừng "đang gõ" TRƯỚC khi gửi tin nhắn phản hồi cuối cùng, không phải sau.
                await _stop_typing()

                if stream_reply:
                    line_buf += thought_filter.finish()
                    line_buf += control_filter.finish()
                    # Gửi nốt phần dư cuối (dòng không có \n kết thúc).
                    line_buf = strip_speaker_prefix(line_buf)
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
                call_model._retry_count = 0
                return final_reply, messages

            if stream_reply:
                line_buf += thought_filter.finish()
                line_buf += control_filter.finish()
                line_buf = strip_speaker_prefix(line_buf)
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
                    "content": strip_speaker_prefix(clean_content),
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
