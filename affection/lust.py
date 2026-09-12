"""
affection/lust.py
~~~~~~~~~~~~~~~~~

Lust management module. Handles lust bar storage, decay, and arousal state.

Lust range: 0-100
- 0-49: Normal
- 50-74: Mild arousal (level 1)
- 75-100: Strong arousal (level 2)

Lust decay: Lazy decay (calculated on fetch, 1% per minute)
Lust cap: 100 (no increase beyond max)
"""

import re
from datetime import datetime, timezone

from client import db
from logger import logger

_LUST_CHANGE_TAG_RE = re.compile(
    r"<lust_change\s+amount\s*=\s*['\"](?P<amount>[+-]?\d+(?:\.\d+)?)['\"]\s*/>",
    re.IGNORECASE,
)

_LUST_RESET_TAG_RE = re.compile(r"<lust\s*/>", re.IGNORECASE)


class LustManager:
    """Manages lust bar for each user."""

    def __init__(self):
        pass

    async def initialize(self):
        """Initialize lust manager."""
        pass

    async def start(self):
        """Start lust manager (no background tasks needed for lazy decay)."""
        pass

    async def shutdown(self):
        """Shutdown lust manager."""
        pass

    async def get_lust(self, user_id: int) -> float:
        """
        Get lust value with lazy decay calculation.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            Lust value (0-100) after decay
        """
        return await db.get_lust(user_id)

    async def increment_lust(self, user_id: int, amount: float):
        """
        Increment lust with clamping to 0-100.
        
        Args:
            user_id: Discord user ID
            amount: Amount to increment (positive)
        """
        await db.increment_lust(user_id, amount)

    async def decrement_lust(self, user_id: int, amount: float):
        """
        Decrement lust with clamping to 0-100.
        
        Args:
            user_id: Discord user ID
            amount: Amount to decrement (positive)
        """
        await db.decrement_lust(user_id, amount)

    async def reset_lust(self, user_id: int):
        """
        Reset lust to 0.
        
        Args:
            user_id: Discord user ID
        """
        await db.reset_lust(user_id)

    def parse_lust_change_tag(self, text: str) -> tuple[float, bool]:
        """
        Parse <lust_change amount="X"/> tags from model response.
        
        Args:
            text: Model response text
            
        Returns:
            Tuple of (total_change, was_reset)
        """
        total_change = 0.0
        was_reset = False
        
        for match in _LUST_CHANGE_TAG_RE.finditer(text):
            try:
                amount = float(match.group("amount"))
                total_change += amount
                logger.debug("[lust] Parsed lust_change tag: %+g", amount)
            except (ValueError, AttributeError):
                logger.warning("[lust] Failed to parse lust_change amount: %s", match.group("amount"))
        
        # Check for lust reset tag
        if _LUST_RESET_TAG_RE.search(text):
            was_reset = True
            logger.debug("[lust] Parsed lust reset tag")
        
        return total_change, was_reset

    def strip_control_tags(self, text: str) -> str:
        """Remove lust control tags before model text is shown to users."""
        return _LUST_CHANGE_TAG_RE.sub("", _LUST_RESET_TAG_RE.sub("", text))

    async def check_aroused_state(self, user_id: int) -> int:
        """
        Check aroused state based on lust value.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            Aroused level: 0 (normal), 1 (mild), 2 (strong)
        """
        lust = await self.get_lust(user_id)
        
        if lust >= 75:
            return 2
        elif lust >= 50:
            return 1
        else:
            return 0

    async def get_aroused_emoji(self, user_id: int) -> str:
        """
        Get emoji for aroused state.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            Emoji: 🔥 (if aroused), empty string otherwise
        """
        aroused_level = await self.check_aroused_state(user_id)
        return "🔥" if aroused_level > 0 else ""


lust = LustManager()