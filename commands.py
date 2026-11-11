import discord

import config
import model_settings
from client import db
from logger import logger
from affection import affection

_MODEL_HELP_LINE = (
    f"`{config.BOT_PREFIX} model` - Đổi model AI của riêng bạn (dropdown)\n"
    if config.MODEL_CHOICES else ""
)

HELP_TEXT = f"""**Lệnh của Hoshino Bot** (prefix: `{config.BOT_PREFIX}`)
`{config.BOT_PREFIX} <nội dung>` - Nói chuyện với Hoshino
`{config.BOT_PREFIX} clear` - Đánh dấu mốc, Hoshino sẽ không đọc lịch sử cũ hơn mốc này nữa
`{config.BOT_PREFIX} addchannel` - Bật auto-respond cho kênh hiện tại (Hoshino trả lời mọi tin nhắn, không cần prefix/mention)
`{config.BOT_PREFIX} removechannel` - Tắt auto-respond cho kênh hiện tại
`{config.BOT_PREFIX} remember <nội dung>` - Nhờ Hoshino ghi nhớ điều gì đó về bạn
`{config.BOT_PREFIX} forget` - Xoá toàn bộ ghi nhớ về bạn
`{config.BOT_PREFIX} reasoning` - Xem reasoning gần nhất mà model đã suy nghĩ ở kênh này (nếu model có hỗ trợ)
{_MODEL_HELP_LINE}`{config.BOT_PREFIX} affection` - Xem mood, bond, lust, experience và quan hệ đặc biệt hiện tại
`{config.BOT_PREFIX} lust reset` - Reset lust về 0 (manual)
`{config.BOT_PREFIX} help` - Xem lại danh sách lệnh này

"""

def make_bar(val: float, max_val: float = 100.0, length: int = 10) -> str:
    val = max(0.0, min(val, max_val))
    filled = round((val / max_val) * length)
    return "▰" * filled + "▱" * (length - filled)


def _model_options(current_model_id: str | None) -> list[discord.SelectOption]:
    options = []
    marked_default = False
    for name, model_id in model_settings.available_models().items():
        is_default = not marked_default and model_id == current_model_id
        if is_default:
            marked_default = True
        options.append(discord.SelectOption(label=name, value=model_id, default=is_default))
    return options


class ModelSelectView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption], owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = int(owner_id)
        self.select = discord.ui.Select(
            placeholder="Chọn model...",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Uhe~... cái dropdown này của người khác mà Sensei.", ephemeral=True
            )
            return

        model_id = self.select.values[0]
        try:
            name = await model_settings.set_model_for_user(self.owner_id, model_id)
        except Exception:
            logger.exception("Không lưu được model đã chọn cho user=%s", self.owner_id)
            await interaction.response.send_message(
                "Lưu model không được rồi Sensei, thử lại sau nha.", ephemeral=True
            )
            return

        if name is None:
            await interaction.response.send_message(
                "Model này không còn trong MODEL_LIST nữa.", ephemeral=True
            )
            return

        marked_default = False
        for option in self.select.options:
            option.default = not marked_default and option.value == model_id
            if option.default:
                marked_default = True
        self.select.disabled = True
        self.stop()
        await interaction.response.edit_message(
            content=f"Đã đổi model của bạn sang **{name}**.", view=self
        )


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
        from affection import bond, special
        await bond.clear_bond(message.author.id)
        special.invalidate(message.author.id)
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

    if lowered == "model":
        if not model_settings.available_models():
            logger.debug("Bỏ qua lệnh model: chưa cấu hình MODEL_LIST")
            return True

        current_id = await model_settings.get_model_for_user(message.author.id)
        current_name = model_settings.name_for(current_id)
        current_line = (
            f"Model hiện tại của bạn: **{current_name}**"
            if current_name
            else "Model hiện tại của bạn: không có trong MODEL_LIST"
        )
        view = ModelSelectView(_model_options(current_id), message.author.id)
        await message.channel.send(
            f"**Chọn model cho riêng bạn**\n{current_line}", view=view
        )
        return True

    if lowered == "lust reset":
        await affection.reset_lust(message.author.id)
        await message.channel.send("Uhe~ đã reset lust của Sensei rồi đó nha~")
        return True

    if lowered in ("affection", "mood", "bond"):
        is_dm = message.guild is None or bool(getattr(message.channel, "nsfw", False))
        d = affection.describe_mood(message.author.id, is_dm=is_dm)
        bond = affection.get_bond(message.author.id)
        bond_rank = affection.get_rank(message.author.id)
        lust = await affection.get_lust(message.author.id)
        exp = await affection.get_experience(message.author.id)
        bond_bar = make_bar(bond, 100.0, 10)
        lust_bar = make_bar(lust, 100.0, 10)
        parts = [
            f"Mood: {d['label']} ({d['valence']:.1f})\n> {d['desc']} {d['blend']}".strip(),
            f"💕 Bond: {bond_bar} {bond:.1f}/100 — {bond_rank}",
        ]
        if d.get("why"):
            parts.append("Lý do: " + "; ".join(d["why"]))
        if is_dm:
            parts.append(f"🔥 Lust: {lust_bar} {lust:.1f}%")
        from affection import experience as experience_mgr, special
        if exp > 0:
            exp_label, exp_desc = experience_mgr.get_experience_label(exp)
            parts.append(f"✨ Experience: {exp:.1f} — {exp_label}: {exp_desc}")
        rel = special.embed_line(message.author.id)
        if rel:
            parts.append(f"💞 Quan hệ đặc biệt: {rel}")
        await message.channel.send("\n".join(parts))
        return True

    return False
