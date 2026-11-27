"""Qua tang cho Hoshino: catalog + dieu kien + cooldown + UI."""
import contextvars
import time
from dataclasses import dataclass, field

import aiosqlite
import discord

from client import db

COOLDOWN_SECONDS = 30 * 60
CUSTOM_DAILY_LIMIT = 2
CUSTOM_MAX_LEN = 100


@dataclass(frozen=True)
class Gift:
    key: str
    name: str
    desc: str
    bond_req: int = 0
    nsfw: bool = False
    bond: float = 0.0
    mood: dict = field(default_factory=dict)
    rel: dict = field(default_factory=dict)
    arousal: float = 0.0
    lust: int = 0


CATALOG = {g.key: g for g in (
    Gift("tra", "Trà / cà phê", "Giúp Hoshino tỉnh táo", 0, False, 1, {"warmth": 5, "fatigue": -5}),
    Gift("banh", "Bánh ngọt", "Món ăn vặt yêu thích", 0, False, 1, {"joy": 8}),
    Gift("goi", "Gối ôm / chăn ấm", "Hợp với người hay ngủ", 0, False, 2, {"warmth": 8, "joy": 5}),
    Gift("hoa", "Hoa", "Một bất ngờ nhỏ", 10, False, 2, {"warmth": 8, "surprise": 3}),
    Gift("gau", "Gấu bông", "Dễ thương, ôm được", 20, False, 3, {"joy": 10, "excitement": 5}),
    Gift("manga", "Manga / game nhỏ", "Giết thời gian cùng nhau", 20, False, 2, {"joy": 10, "excitement": 4}),
    Gift("com", "Bữa ăn tự nấu", "Chăm sóc thật lòng", 25, False, 3, {"warmth": 10, "joy": 6}),
    Gift("nono", "Trả nợ giúp Abydos", "Món quà thực tế nhất", 40, False, 5, {"joy": 12, "warmth": 10}, {"trust": 3}),
    Gift("hen", "Vé đi chơi", "Một buổi hẹn trọn vẹn", 50, False, 4, {"joy": 12, "excitement": 8}, {"openness": 2}),
    Gift("khan", "Khăn len tự đan", "Đồ handmade mất thời gian", 60, False, 4, {"warmth": 12}, {"trust": 3}),
    Gift("nhan", "Nhẫn", "Món quà rất nghiêm túc", 70, False, 5, {"warmth": 12, "excitement": 8}, {"openness": 3}),
    Gift("nuochoa", "Nước hoa / đồ ngủ mỏng", "Chỉ ở kênh NSFW/DM", 40, True, 1, {"warmth": 5}, arousal=15),
    Gift("lingerie", "Lingerie", "Chỉ ở kênh NSFW/DM", 60, True, 2, {"excitement": 8}, arousal=25, lust=1),
    Gift("dochoi", "Đồ chơi người lớn", "Chỉ ở kênh NSFW/DM", 80, True, 0, {"anxiety": 5, "excitement": 10}, arousal=35, lust=1),
)}

_schema_ready = False


async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    async with aiosqlite.connect(db.path) as conn:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS gift_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                gift_key TEXT NOT NULL,
                custom_text TEXT,
                bond_delta REAL NOT NULL DEFAULT 0,
                day TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_user_day ON gift_log(user_id, day)")
        await conn.commit()
    _schema_ready = True


def find(text):
    needle = (text or "").strip().lower()
    for gift in CATALOG.values():
        if needle == gift.key or needle == gift.name.lower():
            return gift
    return None


def option_label(gift, bond):
    lock = "🔒 " if bond < gift.bond_req else ""
    return f"{lock}{gift.name}"[:100]


def option_desc(gift, bond):
    if bond < gift.bond_req:
        return f"Cần Bond {gift.bond_req}+ (hiện {bond:.0f})"[:100]
    return gift.desc[:100]


def check(gift, bond, nsfw):
    if bond < gift.bond_req:
        return f"Chưa đủ Bond, cần {gift.bond_req} (hiện tại {bond:.0f})"
    if gift.nsfw and not nsfw:
        return "Món này chỉ dùng ở kênh NSFW hoặc DM thôi Sensei."
    return None


def clamp_custom_bond(delta):
    return max(-3.0, min(2.0, float(delta)))


def sanitize_custom(text):
    text = (text or "").replace("<", "").replace(">", "")
    text = " ".join(text.split())
    return text[:CUSTOM_MAX_LEN]


async def _day_key():
    from affection.bond import _day_key as day_key
    return day_key()


async def cooldown_left(user_id):
    await _ensure_schema()
    async with aiosqlite.connect(db.path) as conn:
        cur = await conn.execute("SELECT MAX(created_at) FROM gift_log WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
    last = row[0] if row else None
    if not last:
        return 0
    return max(0, int(COOLDOWN_SECONDS - (time.time() - last)))


async def _log(user_id, key, custom_text, bond_delta):
    await _ensure_schema()
    async with aiosqlite.connect(db.path) as conn:
        await conn.execute(
            "INSERT INTO gift_log (user_id, gift_key, custom_text, bond_delta, day, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, key, custom_text, bond_delta, await _day_key(), time.time()),
        )
        await conn.commit()


async def _custom_count(user_id, day):
    await _ensure_schema()
    async with aiosqlite.connect(db.path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM gift_log WHERE user_id = ? AND day = ? AND custom_text IS NOT NULL",
            (user_id, day),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def _has(user_id, key):
    await _ensure_schema()
    async with aiosqlite.connect(db.path) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM gift_log WHERE user_id = ? AND gift_key = ? LIMIT 1", (user_id, key)
        )
        return await cur.fetchone() is not None


def _scope(message):
    return {"is_dm": message.guild is None}


def _is_nsfw(message):
    return message.guild is None or bool(getattr(message.channel, "nsfw", False))


async def _apply(user_id, gift, scope):
    from affection import affection
    from affection import bond as bond_mod
    tag = ""
    if gift.mood:
        tag += "<mood>" + " ".join(f"{k}{v:+g}" for k, v in gift.mood.items()) + "</mood>"
    if gift.rel:
        tag += "<rel>" + " ".join(f"{k}{v:+g}" for k, v in gift.rel.items()) + "</rel>"
    if tag:
        affection.parse_and_apply_mood_tag(
            tag, user_id=user_id, scope=scope, turn_id=f"gift:{time.time()}"
        )
    if gift.bond:
        bond_mod.adjust(user_id, gift.bond)
    if gift.arousal:
        await affection.add_arousal(user_id, gift.arousal)
    if gift.lust:
        await affection.add_lust_level(user_id, gift.lust)


async def _enqueue(message, text, nsfw, custom=False, forced=None):
    from events import _enqueue_request
    from affection import affection
    scope = _scope(message)
    ctx = await affection.on_interaction(message.author.id, scope=scope)
    _enqueue_request(message.channel, {
        "message": message,
        "author": message.author,
        "user_text": text,
        "image_parts": [], "audio_parts": [], "video_parts": [],
        "nsfw": nsfw,
        "affection_context": ctx,
        "reply_context": None,
        "custom_gift": custom,
        "forced_stats": forced,
    })


async def give(message, gift):
    user_id = message.author.id
    nsfw = _is_nsfw(message)
    from affection import bond as bond_mod
    error = check(gift, bond_mod.peek(user_id), nsfw)
    if error:
        await message.channel.send(f"Uhe~... {error}")
        return
    if gift.lust and await _has(user_id, f"lust:{gift.key}"):
        await message.channel.send("Món này em nhận rồi... đừng tặng lại nữa nha Sensei.")
        return
    left = await cooldown_left(user_id)
    if left:
        await message.channel.send(f"Còn {left // 60} phút {left % 60} giây nữa mới tặng tiếp được.")
        return
    await _apply(user_id, gift, _scope(message))
    await _log(user_id, f"lust:{gift.key}" if gift.lust else gift.key, None, gift.bond)
    text = (
        f"Sensei vừa tặng bạn {gift.name} ({gift.desc}). "
        "Hãy phản ứng tự nhiên trong vai Hoshino. "
        "Hiệu ứng đã áp dụng sẵn, KHÔNG xuất tag <bond>/<mood>/<rel>/<lust>."
    )
    await _enqueue(message, text, nsfw, forced={
        "bond": gift.bond, "mood": dict(gift.mood), "rel": dict(gift.rel),
        "arousal": gift.arousal, "lust": gift.lust,
    })


async def give_custom(message, text):
    user_id = message.author.id
    text = sanitize_custom(text)
    if not text:
        await message.channel.send("Sensei chưa nhập gì mà?")
        return
    left = await cooldown_left(user_id)
    if left:
        await message.channel.send(f"Còn {left // 60} phút {left % 60} giây nữa mới tặng tiếp được.")
        return
    day = await _day_key()
    if await _custom_count(user_id, day) >= CUSTOM_DAILY_LIMIT:
        await message.channel.send("Hôm nay Sensei tặng quà tự chọn nhiều rồi, mai nhé.")
        return
    await _log(user_id, "custom", text, 0.0)
    prompt = (
        f'Sensei tặng bạn một món quà tự chọn: "{text}". '
        "Hãy đánh giá món quà và phản ứng trong vai Hoshino. "
        "Bắt buộc kết thúc bằng <bond>N</bond> (N trong khoảng -3..+2) và <mood>...</mood>. "
        "Nếu ngữ cảnh NSFW/DM phù hợp, có thể dùng thêm tag lust/arousal."
    )
    await _enqueue(message, prompt, _is_nsfw(message), custom=True)


class GiftSelect(discord.ui.Select):
    def __init__(self, message, bond, nsfw):
        self.owner = message
        options = [
            discord.SelectOption(label=option_label(g, bond), value=g.key, description=option_desc(g, bond))
            for g in CATALOG.values()
        ]
        super().__init__(placeholder="Chọn quà cho Hoshino...", options=options[:25])

    async def callback(self, interaction):
        if interaction.user.id != self.owner.author.id:
            await interaction.response.send_message("Không phải quà của bạn nha.", ephemeral=True)
            return
        gift = CATALOG[self.values[0]]
        embed = discord.Embed(
            title="Đã tặng quà",
            description=f"**{self.owner.author.display_name}** đã tặng Hoshino **{gift.name}** — {gift.desc}",
        )
        await interaction.response.edit_message(embed=embed, view=None)
        await give(self.owner, gift)


class CustomGiftModal(discord.ui.Modal, title="Quà tự chọn"):
    content = discord.ui.TextInput(label="Bạn tặng gì?", max_length=CUSTOM_MAX_LEN, required=True)

    def __init__(self, message):
        super().__init__()
        self.owner = message

    async def on_submit(self, interaction):
        if interaction.user.id != self.owner.author.id:
            await interaction.response.send_message("Không phải quà của bạn nha.", ephemeral=True)
            return
        text = sanitize_custom(str(self.content))
        embed = discord.Embed(
            title="Đã tặng quà",
            description=f"**{self.owner.author.display_name}** đã tặng Hoshino: \"{text}\"",
        )
        await interaction.response.defer()
        try:
            await interaction.message.edit(embed=embed, view=None)
        except Exception:
            pass
        await give_custom(self.owner, text)


class GiftView(discord.ui.View):
    def __init__(self, message, bond, nsfw):
        super().__init__(timeout=180)
        self.owner = message
        self.add_item(GiftSelect(message, bond, nsfw))
        button = discord.ui.Button(label="Quà tự chọn", style=discord.ButtonStyle.secondary)
        button.callback = self._custom
        self.add_item(button)

    async def _custom(self, interaction):
        if interaction.user.id != self.owner.author.id:
            await interaction.response.send_message("Không phải quà của bạn nha.", ephemeral=True)
            return
        await interaction.response.send_modal(CustomGiftModal(self.owner))


async def handle(message, arg):
    if arg:
        gift = find(arg)
        if gift is not None:
            await give(message, gift)
        else:
            await give_custom(message, arg)
        return
    from affection import bond as bond_mod
    view = GiftView(message, bond_mod.peek(message.author.id), _is_nsfw(message))
    embed = discord.Embed(
        title="Tặng quà cho Hoshino",
        description="Chọn một món bên dưới, hoặc bấm **Quà tự chọn** để nhập món bạn muốn.",
    )
    await message.channel.send(embed=embed, view=view)
