import base64
import mimetypes
import os

import config
from logger import logger


_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"\xff\xd8\xff",        # JPEG
    b"GIF87a",              # GIF
    b"GIF89a",              # GIF
    b"BM",                  # BMP
)

_AUDIO_FORMAT_BY_EXT = {
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "m4a",
    ".ogg": "ogg",
    ".flac": "flac",
    ".aac": "aac",
    ".opus": "opus",
}

_AUDIO_FORMAT_BY_MIME = {
    "mpeg": "mp3",
    "mp3": "mp3",
    "wav": "wav",
    "x-wav": "wav",
    "wave": "wav",
    "mp4": "m4a",
    "x-m4a": "m4a",
    "m4a": "m4a",
    "ogg": "ogg",
    "opus": "opus",
    "flac": "flac",
    "x-flac": "flac",
    "aac": "aac",
}


def _is_image_bytes(raw: bytes, filename: str) -> bool:
    """Xác thực nội dung thực sự là ảnh qua magic bytes, tránh file được
    đổi tên sang .png/.jpg mà không phải ảnh gây 'Unsupported Media Type'."""
    if not raw:
        return False
    for magic in _IMAGE_MAGIC:
        if raw.startswith(magic):
            return True
    if raw.startswith(b"RIFF") and len(raw) > 12 and raw[8:12] == b"WEBP":
        return True
    return False


def _audio_format(att, ctype: str) -> str:
    ext = os.path.splitext(att.filename.lower())[1]
    if ext in _AUDIO_FORMAT_BY_EXT:
        return _AUDIO_FORMAT_BY_EXT[ext]
    if ctype.startswith("audio/"):
        subtype = ctype.split("/", 1)[1].split(";", 1)[0].strip()
        return _AUDIO_FORMAT_BY_MIME.get(subtype, subtype or "mp3")
    return "mp3"


def _video_mime(att, ctype: str) -> str:
    if ctype.startswith("video/"):
        return ctype.split(";", 1)[0].strip()
    return mimetypes.guess_type(att.filename)[0] or "video/mp4"


async def process_attachments(
    message_or_attachments,
    allow_images: bool = True,
    allow_audio: bool = False,
    allow_video: bool = False,
):
    attachments = getattr(message_or_attachments, "attachments", message_or_attachments)
    extra_text_parts = []
    image_parts = []
    audio_parts = []
    video_parts = []

    for att in attachments:
        ctype = (att.content_type or "").lower()
        fname_lower = att.filename.lower()
        ext = os.path.splitext(fname_lower)[1]

        is_image = ctype.startswith("image/") or ext in config.IMAGE_EXTENSIONS
        is_audio = ctype.startswith("audio/") or ext in config.AUDIO_EXTENSIONS
        is_video = ctype.startswith("video/") or ext in config.VIDEO_EXTENSIONS
        is_text = (not is_image and not is_audio and not is_video) and (
            ctype.startswith("text/") or ctype in config.TEXT_EXTRA_MIMES or ext in config.TEXT_EXTENSIONS
        )

        if is_image:
            if not allow_images:
                extra_text_parts.append(
                    f"[Ảnh '{att.filename}' - model hiện tại không xem được ảnh nên đã bỏ qua]"
                )
                continue
            if len(image_parts) >= config.MAX_IMAGES_PER_MESSAGE:
                extra_text_parts.append(f"[Bỏ qua ảnh '{att.filename}' vì đã đủ số ảnh tối đa cho 1 tin nhắn]")
                continue
            if att.size and att.size > config.MAX_IMAGE_BYTES:
                extra_text_parts.append(f"[Ảnh '{att.filename}' quá lớn ({att.size} bytes), bỏ qua]")
                continue
            try:
                raw = await att.read()
                if not _is_image_bytes(raw, att.filename):
                    extra_text_parts.append(
                        f"[Tệp đính kèm '{att.filename}' không phải ảnh hợp lệ, đã bỏ qua]"
                    )
                    continue
                mime = ctype if ctype.startswith("image/") else (mimetypes.guess_type(att.filename)[0] or "image/png")
                b64 = base64.b64encode(raw).decode("ascii")
                image_parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                )
            except Exception as e:
                logger.exception("Lỗi đọc ảnh đính kèm '%s'", att.filename)
                extra_text_parts.append(f"[Lỗi đọc ảnh '{att.filename}': {e}]")

        elif is_audio:
            if not allow_audio:
                extra_text_parts.append(
                    f"[Đính kèm âm thanh '{att.filename}' - model hiện tại không hỗ trợ audio nên đã bỏ qua]"
                )
                continue
            if len(audio_parts) >= config.MAX_AUDIOS_PER_MESSAGE:
                extra_text_parts.append(f"[Bỏ qua âm thanh '{att.filename}' vì đã đủ số audio tối đa cho 1 tin nhắn]")
                continue
            if att.size and att.size > config.MAX_AUDIO_BYTES:
                extra_text_parts.append(f"[Âm thanh '{att.filename}' quá lớn ({att.size} bytes), bỏ qua]")
                continue
            try:
                raw = await att.read()
                b64 = base64.b64encode(raw).decode("ascii")
                audio_parts.append(
                    {
                        "type": "input_audio",
                        "input_audio": {"data": b64, "format": _audio_format(att, ctype)},
                    }
                )
            except Exception as e:
                logger.exception("Lỗi đọc âm thanh đính kèm '%s'", att.filename)
                extra_text_parts.append(f"[Lỗi đọc âm thanh '{att.filename}': {e}]")

        elif is_video:
            if not allow_video:
                extra_text_parts.append(
                    f"[Đính kèm video '{att.filename}' - model hiện tại không hỗ trợ video nên đã bỏ qua]"
                )
                continue
            if len(video_parts) >= config.MAX_VIDEOS_PER_MESSAGE:
                extra_text_parts.append(f"[Bỏ qua video '{att.filename}' vì đã đủ số video tối đa cho 1 tin nhắn]")
                continue
            if att.size and att.size > config.MAX_VIDEO_BYTES:
                extra_text_parts.append(f"[Video '{att.filename}' quá lớn ({att.size} bytes), bỏ qua]")
                continue
            try:
                raw = await att.read()
                mime = _video_mime(att, ctype)
                b64 = base64.b64encode(raw).decode("ascii")
                video_parts.append(
                    {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}}
                )
            except Exception as e:
                logger.exception("Lỗi đọc video đính kèm '%s'", att.filename)
                extra_text_parts.append(f"[Lỗi đọc video '{att.filename}': {e}]")

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

    return "\n\n".join(extra_text_parts), image_parts, audio_parts, video_parts
