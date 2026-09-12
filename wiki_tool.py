"""
Tool cho model đọc tài liệu lore/wiki khi cần context sâu hơn system prompt
(VD: trivia, quan hệ nhân vật, chi tiết trang phục...).

Cách hoạt động:
- Toàn bộ file .md nằm trong thư mục WIKI_DIR (mặc định "wiki/" cạnh file này).
- Enum trong TOOLS_SCHEMA được build TỰ ĐỘNG từ tên file (không phần mở rộng)
  ngay lúc import module này -> chỉ cần thả file .md mới vào thư mục là tool
  tự nhận, không cần sửa code.
- Model gọi tool với đúng 1 trong các tên đó, hàm sẽ đọc lại từ CHÍNH thư mục
  đó (không cho đọc đường dẫn tuỳ ý, tránh path traversal).
"""

import pathlib

from logger import logger

# Đổi tên thư mục ở đây nếu muốn, code còn lại tự chạy theo
WIKI_DIR = pathlib.Path(__file__).parent / "wiki"


def _scan_wiki_files() -> list[str]:
    """Quét WIKI_DIR, trả về danh sách tên file .md (không đuôi), sorted."""
    if not WIKI_DIR.is_dir():
        logger.warning("WIKI_DIR không tồn tại: %s", WIKI_DIR)
        return []
    return sorted(p.stem for p in WIKI_DIR.glob("*.md") if p.is_file())


def _refresh_schema() -> dict:
    """Build lại schema với enum mới nhất từ đĩa."""
    names = _scan_wiki_files()
    return {
        "type": "function",
        "function": {
            "name": "read_wiki",
            "description": "Đọc lore/wiki chi tiết (trivia, quan hệ, trang phục, vũ khí) khi system prompt không đủ. Chỉ dùng khi cần sâu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "enum": names,
                        "description": "Tên file tài liệu.",
                    }
                },
                "required": ["file"],
            },
        },
    }


# Build 1 lần lúc import. Nếu thêm/xoá file trong lúc bot đang chạy và muốn
# enum cập nhật ngay, gọi lại _refresh_schema() rồi gán đè WIKI_TOOL_SCHEMA.
WIKI_TOOL_SCHEMA = _refresh_schema()


async def tool_read_wiki(file: str) -> str:
    logger.info("Tool read_wiki: file=%r", file)
    valid_names = _scan_wiki_files()
    if file not in valid_names:
        return f"[Không có tài liệu tên '{file}'. Các tài liệu hợp lệ: {', '.join(valid_names) or '(trống)'}]"

    path = WIKI_DIR / f"{file}.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.exception("Tool read_wiki lỗi khi đọc file=%r", file)
        return f"[Lỗi đọc tài liệu: {e}]"
