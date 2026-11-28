"""
affection/lust.py
~~~~~~~~~~~~~~~~~
Mô hình 2 lớp giống game hentai:

  * Lust    - tính cách dâm LÂU DÀI, lưu DB theo CẤP ĐỘ 0..LUST_MAX_LEVEL.
              Mặc định KHÔNG decay (LUST_DECAY_PER_DAY = 0), tăng rất chậm.
  * Arousal - hưng phấn TỨC THỜI 0..100, decay theo half-life, reset khi climax.

Tag model được hiểu:
  <lust_change amount="+1"/>      đổi cấp lust (mỗi tag tối đa ±LUST_TAG_MAX, cap/ngày)
  <arousal_change amount="+20"/>  cộng/trừ arousal (tối đa ±AROUSAL_TAG_MAX mỗi tag)
  <climax/>                       reset arousal về 0 (legacy <lust/> cũng hiểu)
  <aroused/>                      legacy, chỉ bị strip
"""

import re

import config as C
from client import db
from logger import logger

_AROUSAL_TAG_RE = re.compile(
    r"<arousal_change\s+amount\s*=\s*['\"](?P<amount>[+-]?\d+(?:\.\d+)?)['\"]\s*/>",
    re.IGNORECASE,
)
_LUST_TAG_RE = re.compile(
    r"<lust_change\s+amount\s*=\s*['\"](?P<amount>[+-]?\d+(?:\.\d+)?)['\"]\s*/>",
    re.IGNORECASE,
)
_CLIMAX_TAG_RE = re.compile(r"<climax\s*/>|<lust\s*/>", re.IGNORECASE)
_AROUSED_TAG_RE = re.compile(r"<aroused\s*/>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(
    r"<arousal_change\b[^>]*/\s*>|<lust_change\b[^>]*/\s*>|<climax\s*/>|<lust\s*/>|<aroused\s*/>",
    re.IGNORECASE,
)


class LustManager:
    """Persistent lust level + transient arousal."""

    async def initialize(self):
        pass

    async def start(self):
        pass

    async def shutdown(self):
        pass

    # Arousal (transient)

    async def get_arousal(self, user_id: int) -> float:
        return await db.get_arousal(user_id)

    async def add_arousal(self, user_id: int, amount: float) -> float:
        return await db.add_arousal(user_id, amount)

    async def reset_arousal(self, user_id: int):
        await db.reset_arousal(user_id)

    def arousal_level(self, value: float) -> int:
        if value >= float(C.AROUSAL_LEVEL2):
            return 2
        if value >= float(C.AROUSAL_LEVEL1):
            return 1
        return 0

    async def get_arousal_level(self, user_id: int) -> int:
        return self.arousal_level(await self.get_arousal(user_id))

    # Lust (persistent level)

    async def get_lust_level(self, user_id: int) -> int:
        return await db.get_lust_level(user_id)

    async def add_lust_level(self, user_id: int, delta: int) -> int:
        return await db.add_lust_level(user_id, delta)

    async def set_lust_level(self, user_id: int, level: int) -> int:
        """Set the persistent lust level directly (bypasses per-tag/daily clamps)."""
        level = max(0, min(int(C.LUST_MAX_LEVEL), int(level)))
        await db.set_lust_level(user_id, level)
        return level

    def lust_name(self, level: int) -> str:
        level = max(0, min(int(C.LUST_MAX_LEVEL), int(level)))
        return C.LUST_LEVEL_NAMES[level]

    def lust_desc(self, level: int) -> str:
        level = max(0, min(int(C.LUST_MAX_LEVEL), int(level)))
        return C.LUST_LEVEL_DESC.get(level, "")

    # Tag parsing

    def parse_tags(self, text: str) -> dict:
        """Return {'arousal': float, 'lust': int, 'climax': bool} from a model reply."""
        arousal = 0.0
        for match in _AROUSAL_TAG_RE.finditer(text):
            try:
                amount = float(match.group("amount"))
            except (ValueError, AttributeError):
                logger.warning("[arousal] bad tag amount: %s", match.group("amount"))
                continue
            arousal += max(-float(C.AROUSAL_TAG_MAX), min(float(C.AROUSAL_TAG_MAX), amount))
        arousal = max(-float(C.AROUSAL_TAG_MAX), min(float(C.AROUSAL_TAG_MAX), arousal))

        lust = 0
        for match in _LUST_TAG_RE.finditer(text):
            try:
                lust += int(round(float(match.group("amount"))))
            except (ValueError, AttributeError):
                logger.warning("[lust] bad tag amount: %s", match.group("amount"))
        lust = max(-int(C.LUST_TAG_MAX), min(int(C.LUST_TAG_MAX), lust))

        return {"arousal": arousal, "lust": lust, "climax": bool(_CLIMAX_TAG_RE.search(text))}

    def strip_tags(self, text: str) -> str:
        """Remove all lust/arousal control tags before showing the reply."""
        return _ANY_TAG_RE.sub("", text)


lust = LustManager()
