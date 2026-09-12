import re


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
