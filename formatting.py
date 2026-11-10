import re

from config import THOUGHT_TAG_PAIRS

_LOWER_TAG_LITERALS = tuple(
    tag.lower() for pair in THOUGHT_TAG_PAIRS for tag in pair
)
_MAX_TAG_LEN = max(len(tag) for tag in _LOWER_TAG_LITERALS)


def _open_tag_regex(opening: str) -> str:
    """Thẻ mở chấp nhận cả kiểu cũ thiếu ``>`` (``<thought\\n...``).

    Một thẻ mở chỉ khớp khi kết thúc bằng ``>`` (có thể có khoảng trắng trước)
    hoặc đứng ngay trước một ranh giới từ, để ``<thoughts>`` không bị nhận nhầm.
    """
    base = opening[:-1] if opening.endswith(">") else opening
    return re.escape(base) + r"(?:\s*>|\b)"


def _close_tag_regex(closing: str) -> str:
    base = closing[:-1] if closing.endswith(">") else closing
    if base and base[-1].isalnum():
        return re.escape(base) + r"(?:\s*>|\b)"
    return re.escape(base) + r"\s*>"


_THOUGHT_TAG_RES = tuple(
    (
        re.compile(_open_tag_regex(opening), re.IGNORECASE),
        re.compile(_close_tag_regex(closing), re.IGNORECASE),
    )
    for opening, closing in THOUGHT_TAG_PAIRS
)


def scan_thought(text: str) -> tuple[str, list[str]]:
    """Tách text thành (phần hội thoại, các khối suy luận).

    Mọi khối suy luận — kể cả khối chưa đóng thẻ hoặc thẻ mở kiểu cũ thiếu
    ``>`` (``<thought\\n...``) — đều bị lấy khỏi phần hội thoại, để thẻ
    reasoning không bao giờ lọt ra Discord."""
    visible: list[str] = []
    thoughts: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        next_open = text.find("<", index)
        if next_open == -1:
            visible.append(text[index:])
            break
        if next_open > index:
            visible.append(text[index:next_open])
            index = next_open

        matched = False
        for open_re, close_re in _THOUGHT_TAG_RES:
            open_match = open_re.match(text, index)
            if not open_match:
                continue
            matched = True
            body_start = open_match.end()
            close_match = close_re.search(text, body_start)
            if close_match is None:
                body = text[body_start:].strip()
                if body:
                    thoughts.append(body)
                return "".join(visible), thoughts
            body = text[body_start:close_match.start()].strip()
            if body:
                thoughts.append(body)
            index = close_match.end()
            break
        if not matched:
            visible.append("<")
            index += 1

    return "".join(visible), thoughts


def cut_partial_tag(text: str) -> str:
    """Cắt phần đuôi text có thể là một thẻ thought chưa gửi hết, để chờ delta
    tiếp theo thay vì lộ thẻ ra giữa chừng."""
    for index in range(max(0, len(text) - _MAX_TAG_LEN), len(text)):
        if text[index] != "<":
            continue
        tail = text[index:].lower()
        if any(literal.startswith(tail) for literal in _LOWER_TAG_LITERALS):
            return text[:index]
    return text


def split_message(text: str, limit: int = 1900) -> list[str]:
    text = text.strip()
    if not text:
        return [text]
    if len(text) <= limit:
        return [text]

    raw_chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1 or cut < limit * 0.3:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit * 0.3:
            cut = remaining.rfind(" ", 0, limit)
        if cut == -1 or cut < limit * 0.3:
            cut = limit
        raw_chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n").lstrip()
    if remaining:
        raw_chunks.append(remaining)

    fixed_chunks: list[str] = []
    open_lang = None
    fence_re = re.compile(r"```(\w*)")

    for chunk in raw_chunks:
        piece = chunk

        if open_lang is not None:
            piece = f"```{open_lang}\n{piece}"

        fence_count = len(fence_re.findall(chunk))
        still_open = (open_lang is not None) != (fence_count % 2 == 1)

        if still_open:
            if open_lang is None:
                matches = fence_re.findall(chunk)
                open_lang = matches[-1] if matches else ""
            piece = piece.rstrip() + "\n```"
        else:
            open_lang = None

        fixed_chunks.append(piece)

    return fixed_chunks
