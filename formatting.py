import re

from config import THOUGHT_TAG_PAIRS

_LOWER_TAG_LITERALS = tuple(
    tag.lower() for pair in THOUGHT_TAG_PAIRS for tag in pair
)
_MAX_TAG_LEN = max(len(tag) for tag in _LOWER_TAG_LITERALS)


def scan_thought(text: str) -> tuple[str, list[str]]:
    """Tách text thành (phần hội thoại, các khối suy luận).

    Mọi khối suy luận — kể cả khối chưa đóng thẻ — đều bị lấy khỏi phần hội
    thoại, để thẻ reasoning không bao giờ lọt ra Discord."""
    lowered = text.lower()
    visible: list[str] = []
    thoughts: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        next_open = lowered.find("<", index)
        if next_open == -1:
            visible.append(text[index:])
            break
        if next_open > index:
            visible.append(text[index:next_open])
            index = next_open

        for opening, closing in THOUGHT_TAG_PAIRS:
            if not lowered.startswith(opening, index):
                continue
            body_start = index + len(opening)
            body_end = lowered.find(closing, body_start)
            body = text[body_start:body_end if body_end != -1 else length].strip()
            if body:
                thoughts.append(body)
            if body_end == -1:
                return "".join(visible), thoughts
            index = body_end + len(closing)
            break
        else:
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
