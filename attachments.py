import base64
import mimetypes
import os

import config
from logger import logger


async def process_attachments(message_or_attachments):
    attachments = getattr(message_or_attachments, "attachments", message_or_attachments)
    extra_text_parts = []
    image_parts = []

    for att in attachments:
        ctype = (att.content_type or "").lower()
        fname_lower = att.filename.lower()
        ext = os.path.splitext(fname_lower)[1]

        is_image = ctype.startswith("image/") or ext in config.IMAGE_EXTENSIONS
        is_audio = ctype.startswith("audio/") or ext in config.AUDIO_EXTENSIONS
        is_text = (not is_image and not is_audio) and (
            ctype.startswith("text/") or ctype in config.TEXT_EXTRA_MIMES or ext in config.TEXT_EXTENSIONS
        )

        if is_image:
            if len(image_parts) >= config.MAX_IMAGES_PER_MESSAGE:
                extra_text_parts.append(f"[Bỏ qua ảnh '{att.filename}' vì đã đủ số ảnh tối đa cho 1 tin nhắn]")
                continue
            if att.size and att.size > config.MAX_IMAGE_BYTES:
                extra_text_parts.append(f"[Ảnh '{att.filename}' quá lớn ({att.size} bytes), bỏ qua]")
                continue
            try:
                raw = await att.read()
                mime = ctype if ctype.startswith("image/") else (mimetypes.guess_type(att.filename)[0] or "image/png")
                b64 = base64.b64encode(raw).decode("ascii")
                image_parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                )
            except Exception as e:
                logger.exception("Lỗi đọc ảnh đính kèm '%s'", att.filename)
                extra_text_parts.append(f"[Lỗi đọc ảnh '{att.filename}': {e}]")

        elif is_audio:
            extra_text_parts.append(
                f"[Đính kèm âm thanh '{att.filename}' - model hiện tại không hỗ trợ audio nên đã bỏ qua]"
            )

        elif is_text:
            try:
                raw = await att.read()
                text = raw.decode("utf-8", errors="replace")
                if len(text) > config.MAX_TEXT_FILE_CHARS:
                    text = text[:config.MAX_TEXT_FILE_CHARS] + "\n...[đã cắt bớt do quá dài]..."
                extra_text_parts.append(f"[Nội dung file '{att.filename}']:\n{text}")
            except Exception as e:
                logger.exception("Lỗi đọc file văn bản đính kèm '%s'", att.filename)
                extra_text_parts.append(f"[Lỗi đọc file '{att.filename}': {e}]")

        else:
            extra_text_parts.append(f"[Tệp đính kèm '{att.filename}' không được hỗ trợ, đã bỏ qua]")

    return "\n\n".join(extra_text_parts), image_parts
