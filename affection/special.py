"""
Special relationship between Hoshino and one user.

Stored as a line in the user's saved information (`user_memory`):

    - Relationship: <label> | <short description>

The model writes it through the existing `update_memory` tool; the backend only
gates and reads it:

  * can only be set while bond >= SPECIAL_MIN_BOND (checked in validate())
  * label / description are sanitised (no <>[] or newlines, length-capped)
  * shown in the affection command + as one line in the <affection> prompt block
  * mild mechanical effects: slower bond idle decay, a bigger share of the user's
    mood leaks into the global mood, a small warmth bonus
  * bond below SPECIAL_STRAINED_BOND -> shown as "strained" (the entry is never
    deleted automatically)
"""

import re
import sqlite3
import time

import config as C

KEY = "relationship"
_CACHE_TTL = 30.0
_cache: dict[int, tuple[float, dict | None]] = {}
_BAD = re.compile(r"[<>\[\]\r\n\t`]+")
_ITEM_RE = re.compile(r"^\s*[-*]?\s*relationship\s*[:：]\s*", re.IGNORECASE)
_LINE_RE = re.compile(r"^\s*[-*]?\s*relationship\s*[:：]\s*(?P<value>.*)$", re.IGNORECASE)


def is_key(key) -> bool:
    return str(key or "").strip().lower() == KEY


def is_relationship_item(text) -> bool:
    """True if a memory add/remove entry targets the relationship line."""
    return bool(_ITEM_RE.match(str(text or "")))


def extract_value(text) -> str:
    """Return the part after `Relationship:` in a memory entry."""
    return _ITEM_RE.sub("", str(text or "")).strip()


def memory_line(value: str) -> str:
    """Canonical line to hand to db.apply_memory_diff."""
    return f"Relationship: {normalize(value)}"


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", _BAD.sub(" ", str(text or ""))).strip()[:limit]


def parse(value: str) -> tuple[str, str]:
    label, _, desc = str(value or "").partition("|")
    return _clean(label, C.SPECIAL_LABEL_MAX), _clean(desc, C.SPECIAL_DESC_MAX)


def normalize(value: str) -> str:
    label, desc = parse(value)
    return f"{label} | {desc}" if desc else label


def validate(uid: int, value: str) -> tuple[str | None, str]:
    """(error, normalized_value). Called before update_memory writes `relationship`."""
    from affection import bond as _b

    label, _ = parse(value)
    if not label:
        return "Error: relationship needs a label, e.g. 'tri kỷ | luôn ở bên Sensei'.", ""
    b = _b.peek(int(uid))
    if b < C.SPECIAL_MIN_BOND:
        return (
            f"Error: bond is {b:.0f}, a special relationship needs at least "
            f"{C.SPECIAL_MIN_BOND:.0f}. Stay in character; do not mention this rule.",
            "",
        )
    return None, normalize(value)


def invalidate(uid: int | None = None) -> None:
    if uid is None:
        _cache.clear()
    else:
        _cache.pop(int(uid), None)


def _read_relationship_line(uid: int) -> str | None:
    """Read the relationship line from user_memory synchronously (small, cached)."""
    try:
        conn = sqlite3.connect(C.AFFECTION_DB_PATH, timeout=2.0)
        try:
            row = conn.execute("SELECT memory FROM user_memory WHERE user_id = ?", (int(uid),)).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    for line in str(row[0]).splitlines():
        match = _LINE_RE.match(line)
        if match:
            return match.group("value")
    return None


def get(uid: int | None, now: float | None = None) -> dict | None:
    """{label, desc, strained} or None."""
    if uid is None:
        return None
    uid, now = int(uid), (time.time() if now is None else now)
    hit = _cache.get(uid)
    if hit and now - hit[0] < _CACHE_TTL:
        return _with_strain(uid, hit[1])
    info = None
    raw = _read_relationship_line(uid)
    if raw:
        label, desc = parse(raw)
        if label:
            info = {"label": label, "desc": desc}
    _cache[uid] = (now, info)
    return _with_strain(uid, info)


def _with_strain(uid: int, info: dict | None) -> dict | None:
    if not info:
        return None
    try:
        from affection import bond as _b
        strained = _b.peek(uid) < C.SPECIAL_STRAINED_BOND
    except Exception:
        strained = False
    return {**info, "strained": strained}


def has(uid: int | None) -> bool:
    info = get(uid)
    return bool(info and not info["strained"])


def decay_mult(uid: int | None) -> float:
    return C.SPECIAL_DECAY_MULT if has(uid) else 1.0


def leak_mult(uid: int | None) -> float:
    return C.SPECIAL_LEAK_MULT if has(uid) else 1.0


def warmth_bonus(uid: int | None) -> float:
    return C.SPECIAL_WARMTH_BONUS if has(uid) else 0.0


def prompt_line(uid: int | None) -> str:
    info = get(uid)
    if not info:
        return ""
    return "Special bond: " + info["label"] + (" (strained)" if info["strained"] else "")


def embed_line(uid: int | None) -> str:
    info = get(uid)
    if not info:
        return ""
    return (
        f"**{info['label']}**"
        + (" *(strained)*" if info["strained"] else "")
        + (f" - {info['desc']}" if info["desc"] else "")
    )
