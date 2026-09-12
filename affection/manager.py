"""
affection/manager.py
~~~~~~~~~~~~~~~~~~~~
Facade. Import this everywhere.

    from affection import affection

Startup:
    await affection.initialize()
    asyncio.create_task(affection.start())

Per message:
    ctx = await affection.on_interaction(message.author.id)
    # ctx is a dict injected into the system prompt.

Mood penalty:
    affection.punish(-15.0)

System prompt line:
    line = affection.prompt_line(message.author.id)
"""

import re
import time
from affection import mood as _mood
from affection import bond as _bond
from affection.lust import lust as _lust
from affection.experience import experience as _experience
from logger import logger

_MOOD_TAG_RE = re.compile(
    r"<mood>\s*(?P<v1>[+-]?\d+(?:\.\d+)?)\s*</mood>"   # canonical:  <mood>-5</mood>
    r"|</mood>\s*\[(?P<v2>[+-]?\d+(?:\.\d+)?)\]"        # malformed:  </mood>[-5]
    r"|</mood>\s*(?P<v3>[+-]?\d+(?:\.\d+)?)>"           # malformed:  </mood>-5>
    r"|</mood>\s*(?P<v4>[+-]?\d+(?:\.\d+)?)\s*</mood>", # malformed:  </mood>25</mood>
    re.IGNORECASE,
)

_SHOCKED_TAG_RE = re.compile(
    r"<shocked>\s*(?P<reason>.+?)\s*</shocked>",   # canonical:  <shocked>Sensei confessed out of nowhere</shocked>
    re.IGNORECASE | re.DOTALL,
)

_AROUSED_TAG_RE = re.compile(r"<aroused\s*/>", re.IGNORECASE)


class AffectionManager:

    def __init__(self):
        self._tasks = set()
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        await _mood.initialize()
        await _bond.initialize()
        self._initialized = True

    async def start(self):
        """Pass to asyncio.create_task()."""
        import asyncio
        if not self._initialized:
            await self.initialize()
        if self._tasks:
            return
        self._tasks = {
            asyncio.create_task(_mood.tick_loop()),
            asyncio.create_task(_bond.health_check_loop()),
        }

    async def shutdown(self):
        """Call during bot shutdown to ensure all pending DB writes complete."""
        if not self._initialized:
            return
        tasks = tuple(self._tasks)
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            import asyncio
            await asyncio.gather(*tasks, return_exceptions=True)
        await _bond.shutdown()
        await _mood.shutdown()

    # Mood (sync — in-memory)

    def get_mood(self) -> float:
        return _mood.get()

    def get_mood_label(self) -> tuple[str, str]:
        return _mood.label()

    def get_mood_emoji(self) -> str:
        """Return the display emoji for the current mood and overlays."""
        if _mood.is_shocked():
            return "😱"
        if _mood.is_sleeping():
            return "😴"
        if _mood.just_woke():
            return "🥱"
        if _mood.is_aroused():
            return "🥵"

        return {
            "distressed": "😭",
            "unhappy": "😞",
            "neutral": "😐",
            "cheerful": "😊",
            "happy": "😄",
        }.get(self.get_mood_label()[0], "😐")

    def nudge_mood(self, delta: float):
        _mood.nudge(delta)

    def punish(self, delta: float):
        """Pass a negative value. Non-blocking."""
        _mood.nudge(delta)

    def parse_and_apply_mood_tag(self, text: str, nsfw: bool = False) -> str:
        """
        Scan model response for <mood>+12</mood> or <mood>-8</mood> tags, and for
        the separate <shocked>reason</shocked> overlay tag. Applies both, then
        strips all tags from the text. Returns the cleaned text to send to the user.

        The model should be instructed (in system prompt) to use these tags
        whenever the conversation warrants it, e.g.:
            <mood>-15</mood>              when the user is rude
            <mood>+8</mood>               when something nice happens
            <shocked>reason here</shocked>  when something genuinely shocks her
        Tags are invisible to the user after stripping.
        """
        for match in _MOOD_TAG_RE.finditer(text):
            try:
                delta = float((match.group("v1") or match.group("v2") or match.group("v3") or match.group("v4")).replace("\u2014", "-").replace("\u2013", "-"))
                # Clamp per-response delta to a reasonable range
                delta = max(-30.0, min(30.0, delta))
                _mood.nudge(delta)
            except ValueError:
                pass
        text = _MOOD_TAG_RE.sub("", text)

        if _AROUSED_TAG_RE.search(text):
            _mood.trigger_aroused(2, source="model")
        text = _AROUSED_TAG_RE.sub("", text)

        shocked_match = _SHOCKED_TAG_RE.search(text)
        if shocked_match:
            _mood.trigger_shocked(shocked_match.group("reason"))
        text = _SHOCKED_TAG_RE.sub("", text)

        return text.strip()

    # Bond (async — DB)

    def get_bond(self, user_id: int) -> float:
        return _bond.get(int(user_id))

    def get_rank(self, user_id: int) -> str:
        return _bond.rank_name(_bond.get(int(user_id)))

    # Lust (sync — DB with lazy decay)

    async def get_lust(self, user_id: int) -> float:
        """Get lust value with lazy decay calculation."""
        return await _lust.get_lust(user_id)

    async def increment_lust(self, user_id: int, amount: float):
        """Increment lust with clamping to 0-100."""
        await _lust.increment_lust(user_id, amount)

    async def decrement_lust(self, user_id: int, amount: float):
        """Decrement lust with clamping to 0-100."""
        await _lust.decrement_lust(user_id, amount)

    async def reset_lust(self, user_id: int):
        """Reset lust to 0."""
        await _lust.reset_lust(user_id)

    def parse_lust_change_tag(self, text: str) -> tuple[float, bool]:
        """
        Parse <lust_change amount="X"/> tags from model response.
        
        Returns:
            Tuple of (total_change, was_reset)
        """
        return _lust.parse_lust_change_tag(text)

    def strip_lust_tags(self, text: str) -> str:
        return _lust.strip_control_tags(text)

    async def check_aroused_state(self, user_id: int) -> int:
        """
        Check aroused state based on lust value.
        
        Returns:
            Aroused level: 0 (normal), 1 (mild), 2 (strong)
        """
        return await _lust.check_aroused_state(user_id)

    async def get_aroused_emoji(self, user_id: int) -> str:
        """Get emoji for aroused state."""
        return await _lust.get_aroused_emoji(user_id)

    # Experience (async — DB)

    async def get_experience(self, user_id: int) -> float:
        """Get experience value."""
        return await _experience.get_experience(user_id)

    async def increment_experience(self, user_id: int, amount: float):
        """Increment experience."""
        await _experience.increment_experience(user_id, amount)

    async def decrement_experience(self, user_id: int, amount: float):
        """Decrement experience."""
        await _experience.decrement_experience(user_id, amount)

    async def reset_experience(self, user_id: int):
        """Reset experience to 0."""
        await _experience.reset_experience(user_id)

    def parse_experience_change_tag(self, text: str) -> float:
        """
        Parse <experience_change amount="X"/> tags from model response.

        Returns:
            Total experience change amount
        """
        return _experience.parse_experience_change_tag(text)

    def strip_experience_tags(self, text: str) -> str:
        return _experience.strip_control_tags(text)

    # Per-message event

    async def on_interaction(self, user_id: int, mood_delta: float = 0.0, nsfw: bool = False) -> dict:
        """
        Call once per incoming user message.

        Returns a context dict to be injected into the system prompt:
            {
                "was_sleeping": bool,
                "mood_label":   str,
                "mood_value":   float,
                "bond_line":    str,
                "lust_value":   float,
                "aroused_level": int,
                "experience_value": float,
            }
        """
        user_id = int(user_id)
        logger.debug("[bond] on_interaction() START user=%s", user_id)
        _mood.wake()
        _mood.maybe_trigger_random_aroused()
        was_sleeping = _mood.just_woke()

        b = _bond.get(user_id)
        logger.debug("[bond] on_interaction() after get(): bond=%.2f", b)
        current_mood = _mood.get()
        exp = _bond.exp_for_message(b, mood=current_mood, mood_delta=mood_delta)
        b = _bond.add_and_get(user_id, exp)  # fire and forget — shutdown waits for all tasks

        if mood_delta != 0.0:
            _mood.nudge(mood_delta)

        lbl, desc = _mood.label()
        aroused = _mood.aroused_state()
        return {
            "was_sleeping": was_sleeping,
            "mood_label":   lbl,
            "mood_desc":  desc,
            "mood_value":   _mood.get(),
            "bond_line":    _bond.prompt_line(b),
            "bond_value":   b,
            "aroused":      aroused,
            "lust_value":   await _lust.get_lust(user_id),
            "aroused_level": await _lust.check_aroused_state(user_id),
            "experience_value": await _experience.get_experience(user_id),
        }

    # Prompt helpers

    def prompt_line(self, user_id: int) -> str:
        """Single relationship line for the system prompt."""
        return _bond.prompt_line(_bond.get(int(user_id)))

    def build_prompt_block(self, ctx: dict, nsfw: bool = False) -> str:
        """
        Convert the dict returned by on_interaction() into a hidden
        system-prompt block. Wrap this in your system instruction.

        Example output injected into prompt:
            <affection>
            Relationship with this user: Friend (Bond 31.2/100)
            Hoshino's current mood: cheerful (22.4)
            Hoshino just woke up from sleep. React naturally as if just woken up.
            </affection>
        """
        lines = [
            ctx["bond_line"],
            f"Hoshino's current mood: {ctx['mood_label']} ({ctx['mood_value']:.1f}) — {ctx['mood_desc']}",
        ]
        aroused = ctx.get("aroused", {})
        aroused_level = aroused.get("level", 0)
        if aroused_level:
            if not nsfw:
                lines.append(
                    "Hoshino feels mildly flustered and playful. Let this affect the tone subtly, "
                    "with only shy teasing; keep all content non-sexual and do not make this state the topic."
                )
            else:
                bond_allows_proactive = ctx.get("bond_value", 0.0) >= _mood.AROUSED_BOND_THRESHOLD
                lines.append(
                    f"Hoshino's private aroused state is level {aroused_level}. "
                    "This state is internal and must never be mentioned to Sensei."
                )
                if aroused_level == 1:
                    lines.append("It is only mild; let it affect tone subtly and do not force intimacy into unrelated replies.")
                elif bond_allows_proactive:
                    lines.append(
                        "The bond is high enough for proactive flirting or an invitation to intimacy only when "
                        "it naturally fits the conversation; do not force sexual content into unrelated replies."
                    )
                else:
                    lines.append("She may respond to intimate cues, but must not proactively initiate sexual content at this bond.")

        if nsfw:
            lines.append(
                "If the conversation genuinely causes a clear change into this private state, append exactly "
                "<aroused/>; this tag is internal and must not be explained to Sensei."
            )
        if ctx["was_sleeping"]:
            lines.append("Hoshino recently woke up — she may seem slightly drowsy. Mention it at most once subtly, do not dwell on it.")

        # Add experience to prompt block
        exp_value = ctx.get("experience_value", 0.0)
        if exp_value > 0:
            exp_label, exp_desc = _experience.get_experience_label(exp_value)
            lines.append(f"Treasured memories with Sensei: {exp_label} ({exp_value:.1f}) — {exp_desc}")

        return "<affection>\n" + "\n".join(lines) + "\n</affection>"


affection = AffectionManager()