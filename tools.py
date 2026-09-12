import asyncio
import re

import aiohttp

import config
from client import db
from logger import logger
from wiki_tool import WIKI_TOOL_SCHEMA, tool_read_wiki


def _run_ddgs_search(query: str, max_results: int = 5):
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return results


async def tool_web_search(query: str, max_results: int = 5) -> str:
    logger.info("Tool web_search: query=%r max_results=%s", query, max_results)
    try:
        results = await asyncio.to_thread(_run_ddgs_search, query, max_results)
    except Exception as e:
        logger.exception("Tool web_search lỗi với query=%r", query)
        return f"[Lỗi tìm kiếm: {e}]"

    if not results:
        return "Không tìm thấy kết quả nào."

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        href = r.get("href") or r.get("link", "")
        body = r.get("body", "")
        lines.append(f"{i}. {title}\n{href}\n{body}")
    return "\n\n".join(lines)


async def tool_web_crawl(url: str) -> str:
    if not re.match(r"^https?://", url):
        url = "https://" + url
    jina_url = f"https://r.jina.ai/{url}"
    headers = {}
    if config.JINA_API_KEY:
        headers["Authorization"] = f"Bearer {config.JINA_API_KEY}"

    logger.info("Tool web_crawl: url=%r", url)
    try:
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(jina_url, headers=headers) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.warning("Tool web_crawl trả về status %s cho url=%r", resp.status, url)
                    return f"[Lỗi crawl ({resp.status}): {text[:300]}]"
                return text[:6000]
    except Exception as e:
        logger.exception("Tool web_crawl lỗi với url=%r", url)
        return f"[Lỗi crawl: {e}]"


async def tool_search_history(
    author_id: int, query: str = None, scope: str = "all", channel_id: int = None, guild_id: int = None,
    channel_name: str = None, guild_name: str = None, limit: int = 10,
) -> str:
    filter_channel_id = channel_id if scope == "channel" else None
    filter_guild_id = guild_id if scope == "guild" else None
    logger.info(
        "Tool search_history: author_id=%s query=%r scope=%s channel_name=%r guild_name=%r limit=%s",
        author_id, query, scope, channel_name, guild_name, limit,
    )
    rows = await db.search_global_history(
        author_id, query=query, channel_id=filter_channel_id, guild_id=filter_guild_id,
        channel_name=channel_name, guild_name=guild_name, limit=limit,
    )
    if not rows:
        return "Không tìm thấy gì trong lịch sử trò chuyện trước đây."

    lines = [
        f"[{r['created_at']} | #{r.get('channel_name') or '?'} @ {r.get('guild_name') or 'DM'}] ({r['role']}) {r['content']}"
        for r in rows
    ]
    return "\n".join(lines)


TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Tìm tin tức, sự kiện mới hoặc dữ liệu chưa rõ trên internet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Từ khóa tìm kiếm."},
                    "max_results": {"type": "integer", "description": "Số kết quả (mặc định 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_crawl",
            "description": "Đọc toàn bộ nội dung văn bản của 1 URL qua Jina Reader.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL cần đọc."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "Cập nhật bộ nhớ về Sensei. Khi đổi tên: lưu add='Sensei nickname: <tên>' hoặc 'Hoshino nickname: <tên>', xóm tên cũ ở remove trong cùng lượt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "add": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ghi nhớ mới ngắn gọn.",
                    },
                    "remove": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Từ khóa khớp để xóa ghi nhớ cũ.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "increase_output_tokens",
            "description": "Tăng giới hạn output lượt kế nếu 512 token không đủ trả lời dài.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_tokens": {
                        "type": "integer",
                        "description": "Giới hạn output mới (vd: 16384, 32768).",
                    }
                },
                "required": ["max_tokens"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": "Tìm trong lịch sử chat cũ/ngoài kênh với Sensei.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Từ khóa tìm kiếm (bỏ trống = lấy tin gần nhất).",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["all", "channel", "guild"],
                        "description": "Phạm vi: 'all' (mặc định), 'channel', 'guild'.",
                    },
                    "channel_name": {
                        "type": "string",
                        "description": "Tên kênh cụ thể.",
                    },
                    "guild_name": {
                        "type": "string",
                        "description": "Tên server cụ thể.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Số kết quả tối đa (mặc định 10).",
                    },
                },
                "required": [],
            },
        },
    },
    WIKI_TOOL_SCHEMA,
]

async def execute_tool_call(name: str, args: dict, author_id: int, channel_id: int = None, guild_id: int = None) -> str:
    logger.info("Execute tool '%s' cho author_id=%s args=%s", name, author_id, args)
    if name == "web_search":
        return await tool_web_search(args.get("query", ""), int(args.get("max_results", 5) or 5))
    if name == "web_crawl":
        return await tool_web_crawl(args.get("url", ""))
    if name == "update_memory":
        add = args.get("add") or []
        remove = args.get("remove") or []
        if isinstance(add, str):
            add = [add]
        if isinstance(remove, str):
            remove = [remove]
        # Tương thích ngược nếu model vẫn gọi với memory_text kiểu cũ
        legacy_text = args.get("memory_text", "").strip()
        if legacy_text:
            add = [*add, legacy_text]

        if not add and not remove:
            return "Không có nội dung để cập nhật."

        new_memory = await db.apply_memory_diff(author_id, add=add, remove=remove)
        logger.info(
            "Đã cập nhật memory (diff) cho author_id=%s: add=%s remove=%s", author_id, add, remove
        )
        return f"Đã cập nhật ghi nhớ.\n\nGhi nhớ hiện tại:\n{new_memory or '(trống)'}"
    if name == "increase_output_tokens":
        return "Đã tăng giới hạn output cho lượt kế tiếp."
    if name == "read_wiki":
        return await tool_read_wiki(args.get("file", ""))
    if name == "search_history":
        return await tool_search_history(
            author_id,
            query=args.get("query") or None,
            scope=args.get("scope", "all"),
            channel_id=channel_id,
            guild_id=guild_id,
            channel_name=args.get("channel_name") or None,
            guild_name=args.get("guild_name") or None,
            limit=int(args.get("limit", 10) or 10),
        )
    logger.warning("Model gọi tool không tồn tại: %r", name)
    return f"[Không có tool tên '{name}']"
