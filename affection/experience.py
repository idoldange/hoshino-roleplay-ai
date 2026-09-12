"""
affection/experience.py
~~~~~~~~~~~~~~~~~~~~~~~

Experience/Treasured memories management module. Handles experience bar storage
and parsing of output tags.

Experience range: 0+
- 0-24: Newcomer (New memories forming)
- 25-49: Familiar (Some shared experiences)
- 50-74: Deep (Many treasured memories)
- 75-99: Intimate (Deep bond, many significant moments)
- 100+: Soulmate (Lifetime of treasured memories)

Experience is permanent and only increases (no decay).
"""

import re

from client import db
from logger import logger

_EXPERIENCE_CHANGE_TAG_RE = re.compile(
    r"<experience_change\s+amount\s*=\s*['\"](?P<amount>[+-]?\d+(?:\.\d+)?)['\"]\s*/>",
    re.IGNORECASE,
)


class ExperienceManager:
    """Manages experience/treasured memories for each user."""

    def __init__(self):
        pass

    async def initialize(self):
        """Initialize experience manager."""
        pass

    async def start(self):
        """Start experience manager (no background tasks needed)."""
        pass

    async def shutdown(self):
        """Shutdown experience manager."""
        pass

    async def get_experience(self, user_id: int) -> float:
        """
        Get experience value.

        Args:
            user_id: Discord user ID

        Returns:
            Experience value (0+)
        """
        return await db.get_experience(user_id)

    async def increment_experience(self, user_id: int, amount: float):
        """
        Increment experience with clamping to >= 0.

        Args:
            user_id: Discord user ID
            amount: Amount to increment (positive)
        """
        await db.increment_experience(user_id, amount)

    async def decrement_experience(self, user_id: int, amount: float):
        """
        Decrement experience with clamping to >= 0.

        Args:
            user_id: Discord user ID
            amount: Amount to decrement (positive)
        """
        current = await self.get_experience(user_id)
        new_value = max(0.0, current - amount)
        await db.set_experience(user_id, new_value)

    async def reset_experience(self, user_id: int):
        """
        Reset experience to 0.

        Args:
            user_id: Discord user ID
        """
        await db.clear_experience(user_id)

    def parse_experience_change_tag(self, text: str) -> float:
        """
        Parse <experience_change amount="X"/> tags from model response.

        Args:
            text: Model response text

        Returns:
            Total experience change amount
        """
        total_change = 0.0

        for match in _EXPERIENCE_CHANGE_TAG_RE.finditer(text):
            try:
                amount = float(match.group("amount"))
                total_change += amount
                logger.debug("[experience] Parsed experience_change tag: %+g", amount)
            except (ValueError, AttributeError):
                logger.warning("[experience] Failed to parse experience_change amount: %s", match.group("amount"))

        return total_change

    def strip_control_tags(self, text: str) -> str:
        """Remove experience control tags before model text is shown to users."""
        return _EXPERIENCE_CHANGE_TAG_RE.sub("", text)

    def get_experience_label(self, experience: float) -> tuple[str, str]:
        """
        Get label and description for experience level.

        Returns:
            Tuple of (label, description)
        """
        if experience >= 100:
            return "Soulmate", "Lifetime of treasured memories together"
        elif experience >= 75:
            return "Intimate", "Deep bond, many significant moments"
        elif experience >= 50:
            return "Deep", "Many treasured memories"
        elif experience >= 25:
            return "Familiar", "Some shared experiences"
        else:
            return "Newcomer", "New memories forming"

    def make_bar(self, val: float, max_val: float = 100.0, length: int = 10) -> str:
        """Create a visual bar for experience."""
        val = max(0.0, min(val, max_val))
        filled = round((val / max_val) * length)
        return "▰" * filled + "▱" * (length - filled)


experience = ExperienceManager()