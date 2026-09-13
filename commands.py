import discord

import config
from client import db
from logger import logger
from affection import affection

HELP_TEXT = f"""**Lệnh của Hoshino Bot** (prefix: `{config.BOT_PREFIX}`)
`{config.BOT_PREFIX} <nội dung>` - Nói chuyện với Hoshino
`{config.BOT_PREFIX} clear` - Đánh dấu mốc, Hoshino sẽ không đọc lịch sử cũ hơn mốc này nữa
`{config.BOT_PREFIX} addchannel` - Bật auto-respond cho kênh hiện tại (Hoshino trả lời mọi tin nhắn, không cần prefix/mention)
`{config.BOT_PREFIX} removechannel` - Tắt auto-respond cho kênh hiện tại
`{config.BOT_PREFIX} remember <nội dung>` - Nhờ Hoshino ghi nhớ điều gì đó về bạn
`{config.BOT_PREFIX} forget` - Xoá toàn bộ ghi nhớ về bạn
`{config.BOT_PREFIX} reasoning` - Xem reasoning gần nhất mà model đã suy nghĩ ở kênh này (nếu model có hỗ trợ)
`{config.BOT_PREFIX} affection` - Xem mood, bond và lust hiện tại
`{config.BOT_PREFIX} lust reset` - Reset lust về 0 (manual)
`{config.BOT_PREFIX} help` - Xem lại danh sách lệnh này

"""

def make_bar(val: float, max_val: float = 100.0, length: int = 10) -> str:
    val = max(0.0, min(val, max_val))
    filled = round((val / max_val) * length)
    return "▰" * filled + "▱" * (length - filled)

async def handle_command(message: discord.Message, user_text: str) -> bool:
    lowered = user_text.lower().strip()
    logger.info(
        "[CMD] %s (%s) trong channel=%s: %r",
        message.author, message.author.id, message.channel.id, lowered,
    )

    if lowered == "clear":
        await message.channel.send(f"Uhe~, ok Sensei, em xoá trí nhớ tạm ở đây từ giờ trở đi nha!{config.CLEAR_MARKER}")
        return True

    if lowered == "help":
        await message.channel.send(HELP_TEXT)
        return True

    if lowered in ("addchannel", "add channel"):
        perms = getattr(message.author, "guild_permissions", None)
        if not message.guild or not (perms and perms.manage_guild):
            await message.channel.send("Uhe~... cái này cần quyền quản lý server mới bật được á Sensei.")
            return True
        await db.add_auto_channel(message.channel.id, message.guild.id)
        await message.channel.send(f"Đã bật auto-respond ở kênh này. Giờ em sẽ trả lời mọi tin nhắn ở đây luôn~")
        return True

    if lowered in ("removechannel", "remove channel"):
        perms = getattr(message.author, "guild_permissions", None)
        if not message.guild or not (perms and perms.manage_guild):
            await message.channel.send("Uhe~... cái này cần quyền quản lý server mới tắt được á Sensei.")
            return True
        removed = await db.remove_auto_channel(message.channel.id)
        if removed:
            await message.channel.send("Đã tắt auto-respond ở kênh này rồi nha.")
        else:
            await message.channel.send("Kênh này đâu có bật auto-respond đâu Sensei?")
        return True

    if lowered.startswith("remember "):
        note = user_text[len("remember "):].strip()
        if note:
            await db.append_memory(message.author.id, note)
            await message.channel.send("Uhe~ em nhớ rồi đó nha.")
        else:
            await message.channel.send("Nhớ... cái gì cơ Sensei? Chưa thấy nói gì hết á.")
        return True

    if lowered == "forget":
        await db.clear_memory(message.author.id)
        await db.clear_personalization(message.author.id)
        await db.clear_global_history(message.author.id)
        from affection import bond
        await bond.clear_bond(message.author.id)
        await message.channel.send("Xoá sạch ghi nhớ về Sensei rồi đó, coi như tụi mình mới quen lại từ đầu ha~")
        return True

    if lowered == "reasoning":
        row = await db.get_last_reasoning(message.channel.id)
        if not row:
            await message.channel.send("Chưa có reasoning nào được lưu ở kênh này (model có thể không hỗ trợ, hoặc chưa trả lời lần nào).")
        else:
            text = row["reasoning"]
            if len(text) > 1800:
                text = text[:1800] + "\n...[cắt bớt]..."
            await message.channel.send(f"🧠 Reasoning gần nhất ({row['created_at']}):\n```{text}```")
        return True

    if lowered == "lust reset":
        await affection.reset_lust(message.author.id)
        await message.channel.send("Uhe~ đã reset lust của Sensei rồi đó nha~")
        return True

    if lowered in ("affection", "mood", "bond"):
        mood_label, mood_desc = affection.get_mood_label()
        bond = affection.get_bond(message.author.id)
        bond_rank = affection.get_rank(message.author.id)
        lust = await affection.get_lust(message.author.id)
        mood_emoji = affection.get_mood_emoji()
        bond_bar = make_bar(bond, 100.0, 10)
        lust_bar = make_bar(lust, 100.0, 10)
        parts = [ 
            f"{mood_emoji} Mood: {mood_label} ({affection.get_mood():.1f})\n> {mood_desc}",
            f"💕 Bond: {bond_bar} {int(bond)}/100",
        ]
        if message.guild is None or bool(getattr(message.channel, "nsfw", False)):
            aroused_emoji = await affection.get_aroused_emoji(message.author.id)
            parts.append(f"{aroused_emoji}🔥 Lust: {lust_bar} {lust:.1f}%")
        
        await message.channel.send("\n".join(parts))
        return True

    return False
