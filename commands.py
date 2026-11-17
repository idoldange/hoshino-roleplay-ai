import re

import discord

import config
import endpoint_profiles
import model_settings
from client import db
from logger import logger
from affection import affection

_MODEL_HELP_LINE = f"`{config.BOT_PREFIX} model` - Đổi model AI của riêng bạn (dropdown)\n"
_CONFIG_HELP_LINE = (
    f"`{config.BOT_PREFIX} config` - Cấu hình endpoint/model riêng bằng embed và modal\n"
)

_MOOD_VI = {
    "default": "Bình thường", "happy": "Vui vẻ", "delighted": "Rất vui",
    "motivated": "Hứng khởi", "sad": "Buồn", "angered": "Đang giận", "shocked": "Sốc",
}
_EMO_VI = {
    "joy": "vui vẻ", "warmth": "ấm áp", "excitement": "háo hức", "sadness": "buồn bã",
    "anger": "tức giận", "anxiety": "lo lắng", "surprise": "ngạc nhiên", "fatigue": "mệt mỏi",
    "curiosity": "tò mò",
}
_REL_AXIS_VI = {
    "trust": "tin tưởng", "respect": "tôn trọng", "tension": "căng thẳng",
    "playfulness": "tinh nghịch", "ease": "thoải mái", "openness": "cởi mở",
}
_REL_WORD_VI = {"very high": "rất cao", "high": "cao", "low": "thấp", "very low": "rất thấp"}
_INTENSITY_VI = ((0.75, "mãnh liệt"), (0.55, "mạnh"), (0.35, "vừa"), (0.15, "nhẹ"))
_AGE_VI = (
    (re.compile(r"\bjust now\b"), "vừa xong"),
    (re.compile(r"~(\d+)m ago"), r"~\1 phút trước"),
    (re.compile(r"~(\d+)h ago"), r"~\1 giờ trước"),
)


def _vi_intensity(x: float) -> str:
    for threshold, word in _INTENSITY_VI:
        if x >= threshold:
            return word
    return "thoáng qua"


def _vi_blend(vec: dict, dom: list[str]) -> str:
    if not dom:
        return "Tâm trạng khá bình ổn"
    out = f"Chủ yếu là {_EMO_VI.get(dom[0], dom[0])} ({_vi_intensity(vec.get(dom[0], 0.0))})"
    if len(dom) > 1:
        out += f", pha chút {_EMO_VI.get(dom[1], dom[1])} ({_vi_intensity(vec.get(dom[1], 0.0))})"
    if "fatigue" not in dom and vec.get("fatigue", 0.0) >= 0.30:
        out += ", cũng hơi mệt mỏi"
    return out + "."


def _vi_why(line: str) -> str:
    match = re.match(r"^([a-z]+): ", line)
    if match and match.group(1) in _EMO_VI:
        line = _EMO_VI[match.group(1)] + ": " + line[match.end():]
    line = line.replace("a private matter", "chuyện riêng tư")
    for pattern, replacement in _AGE_VI:
        line = pattern.sub(replacement, line)
    return line

HELP_TEXT = f"""**Lệnh của Hoshino Bot** (prefix: `{config.BOT_PREFIX}`)
`{config.BOT_PREFIX} <nội dung>` - Nói chuyện với Hoshino
{_CONFIG_HELP_LINE}{_MODEL_HELP_LINE}`{config.BOT_PREFIX} clear` - Đánh dấu mốc, Hoshino sẽ không đọc lịch sử cũ hơn mốc này nữa
`{config.BOT_PREFIX} addchannel` - Bật auto-respond cho kênh hiện tại (Hoshino trả lời mọi tin nhắn, không cần prefix/mention)
`{config.BOT_PREFIX} removechannel` - Tắt auto-respond cho kênh hiện tại
`{config.BOT_PREFIX} remember <nội dung>` - Nhờ Hoshino ghi nhớ điều gì đó về bạn
`{config.BOT_PREFIX} forget` - Xoá toàn bộ ghi nhớ về bạn
`{config.BOT_PREFIX} reasoning` - Xem reasoning gần nhất mà model đã suy nghĩ ở kênh này (nếu model có hỗ trợ)
`{config.BOT_PREFIX} affection` - Xem mood, bond, lust, experience và quan hệ đặc biệt hiện tại
`{config.BOT_PREFIX} lust reset` - Reset lust về 0 (manual)
`{config.BOT_PREFIX} help` - Xem lại danh sách lệnh này

"""

def make_bar(val: float, max_val: float = 100.0, length: int = 10) -> str:
    val = max(0.0, min(val, max_val))
    filled = round((val / max_val) * length)
    return "▰" * filled + "▱" * (length - filled)


class ModelSelectView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, models: dict[str, dict], owner_id: int, current_model_name: str | None):
        super().__init__(timeout=180)
        self.owner_id = int(owner_id)
        self.models = list(models.items())
        self.current_model_name = current_model_name
        current_index = next(
            (index for index, (name, _) in enumerate(self.models) if name == current_model_name),
            0,
        )
        self.page = current_index // self.PAGE_SIZE
        self._build_items()

    def _build_items(self):
        self.clear_items()
        start = self.page * self.PAGE_SIZE
        entries = self.models[start:start + self.PAGE_SIZE]
        options = [
            discord.SelectOption(
                label=name[:100],
                value=name,
                description=(info.get("description") or (
                    "Nhận ảnh" if info.get("multimodal", True) else "Chỉ text"
                ))[:100],
                default=name == self.current_model_name,
            )
            for name, info in entries
        ]
        select = discord.ui.Select(
            placeholder="Chọn model...",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._on_select
        self.add_item(select)
        if self.page > 0:
            previous = discord.ui.Button(label="Trước", style=discord.ButtonStyle.secondary)
            previous.callback = self._previous_page
            self.add_item(previous)
        if start + self.PAGE_SIZE < len(self.models):
            following = discord.ui.Button(label="Tiếp", style=discord.ButtonStyle.secondary)
            following.callback = self._next_page
            self.add_item(following)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Uhe~... cái dropdown này của người khác mà Sensei.", ephemeral=True
            )
            return False
        return True

    async def _change_page(self, interaction: discord.Interaction, step: int):
        self.page += step
        self._build_items()
        await interaction.response.edit_message(view=self)

    async def _previous_page(self, interaction: discord.Interaction):
        await self._change_page(interaction, -1)

    async def _next_page(self, interaction: discord.Interaction):
        await self._change_page(interaction, 1)

    async def _on_select(self, interaction: discord.Interaction):
        selection = interaction.data["values"][0]
        try:
            name = await model_settings.set_model_for_user(self.owner_id, selection)
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

        self.stop()
        await interaction.response.edit_message(
            content=f"Đã đổi model của bạn sang **{name}**.", view=None
        )


def _config_embed(profiles: list[dict], encryption_ready: bool) -> discord.Embed:
    embed = discord.Embed(
        title="Cấu hình endpoint riêng",
        description=(
            "Chọn profile rồi bấm **Thêm / sửa**. API key chỉ nhập trong modal và được mã hóa trước khi lưu DB."
        ),
        color=discord.Color.blue(),
    )
    if not encryption_ready:
        embed.description += (
            "\n\n⚠️ Chưa cấu hình `CUSTOM_ENDPOINT_ENCRYPTION_KEY` trong `.env`; "
            "hãy đặt secret trước khi lưu profile."
        )
    if profiles:
        lines = [
            f"• **{profile['name']}** — {len(profile['models'])} model"
            for profile in profiles
        ]
        embed.add_field(name="Profile của bạn", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Profile của bạn",
            value="Chưa có profile. Bấm **Thêm / sửa** để tạo.",
            inline=False,
        )
    embed.add_field(
        name="Multimodal",
        value="Tự đoán theo model ID; trong modal có thể đặt `model-id=true`, `false` hoặc `auto`.",
        inline=False,
    )
    embed.set_footer(text="Model sẽ tự dò từ endpoint; model riêng hiện trong !hoshino model.")
    return embed


class EndpointProfileModal(discord.ui.Modal, title="Cấu hình endpoint profile"):
    def __init__(self, owner_id: int, config_view, profile: dict | None = None):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.config_view = config_view
        self.profile_id = profile.get("id") if profile else None
        self.name_input = discord.ui.TextInput(
            label="Tên profile",
            placeholder="Ví dụ: OpenRouter",
            default=profile.get("name", "") if profile else None,
            max_length=40,
        )
        self.endpoint_input = discord.ui.TextInput(
            label="OpenAI-compatible endpoint",
            placeholder="https://host.example/v1",
            default=profile.get("base_url", "") if profile else None,
            max_length=500,
        )
        self.keys_input = discord.ui.TextInput(
            label="API key (nhiều key cách nhau bằng dấu phẩy)",
            placeholder="Để trống khi sửa nếu muốn giữ key hiện tại",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        overrides = profile.get("overrides", {}) if profile else {}
        override_text = "\n".join(
            f"{model_id}={'true' if enabled else 'false'}"
            for model_id, enabled in overrides.items()
        )
        self.overrides_input = discord.ui.TextInput(
            label="Override multimodal (không bắt buộc)",
            placeholder="Mỗi dòng: model-id=true/false/auto",
            default=override_text[:4000] if override_text else None,
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.name_input)
        self.add_item(self.endpoint_input)
        self.add_item(self.keys_input)
        self.add_item(self.overrides_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            profile, model_count = await endpoint_profiles.save_profile(
                self.owner_id,
                self.profile_id,
                self.name_input.value,
                self.endpoint_input.value,
                self.keys_input.value,
                self.overrides_input.value,
            )
            model_settings.invalidate_user_cache(self.owner_id)
            self.config_view.profiles = await endpoint_profiles.list_profiles(self.owner_id)
            self.config_view.selected_profile_id = profile["id"]
            self.config_view._build_items()
            if self.config_view.config_message:
                await self.config_view.config_message.edit(
                    embed=_config_embed(
                        self.config_view.profiles,
                        endpoint_profiles.encryption_configured(),
                    ),
                    view=self.config_view,
                )
            action = "Cập nhật" if self.profile_id else "Tạo"
            await interaction.followup.send(
                f"{action} profile **{profile['name']}** thành công; đã dò được {model_count} model. "
                f"Dùng `{config.BOT_PREFIX} model` để chọn model.",
                ephemeral=True,
            )
        except endpoint_profiles.ProfileStoreError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception:
            logger.exception("Không lưu được endpoint profile của user=%s", self.owner_id)
            await interaction.followup.send(
                "Lưu profile không thành công. Kiểm tra cấu hình endpoint rồi thử lại.",
                ephemeral=True,
            )


class DeleteProfileView(discord.ui.View):
    def __init__(self, config_view, profile_id: str, owner_id: int):
        super().__init__(timeout=60)
        self.config_view = config_view
        self.profile_id = profile_id
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Bảng xác nhận này không phải của bạn.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Xóa profile", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            name = await endpoint_profiles.delete_profile(self.owner_id, self.profile_id)
            model_settings.invalidate_user_cache(self.owner_id)
            self.config_view.profiles = await endpoint_profiles.list_profiles(self.owner_id)
            self.config_view.selected_profile_id = None
            self.config_view._build_items()
            if self.config_view.config_message:
                await self.config_view.config_message.edit(
                    embed=_config_embed(
                        self.config_view.profiles,
                        endpoint_profiles.encryption_configured(),
                    ),
                    view=self.config_view,
                )
            await interaction.followup.send(f"Đã xóa profile **{name}**.", ephemeral=True)
        except Exception:
            logger.exception("Không xóa được endpoint profile của user=%s", self.owner_id)
            await interaction.followup.send("Xóa profile không thành công.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Hủy", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Đã hủy.", view=None)
        self.stop()


class EndpointConfigView(discord.ui.View):
    def __init__(self, owner_id: int, profiles: list[dict]):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.profiles = profiles
        self.selected_profile_id = None
        self.config_message = None
        self._build_items()

    def _build_items(self):
        self.clear_items()
        if self.profiles:
            options = [
                discord.SelectOption(
                    label=profile["name"],
                    value=profile["id"],
                    description=f"{len(profile['models'])} model",
                    default=profile["id"] == self.selected_profile_id,
                )
                for profile in self.profiles
            ]
            select = discord.ui.Select(
                placeholder="Chọn profile để sửa hoặc xóa...",
                options=options,
                min_values=1,
                max_values=1,
            )
            select.callback = self._select_profile
            self.add_item(select)

        edit_button = discord.ui.Button(
            label="Thêm / sửa profile",
            style=discord.ButtonStyle.primary,
            disabled=not endpoint_profiles.encryption_configured(),
        )
        edit_button.callback = self._edit_profile
        self.add_item(edit_button)
        if self.profiles:
            delete_button = discord.ui.Button(
                label="Xóa profile",
                style=discord.ButtonStyle.danger,
                disabled=self.selected_profile_id is None,
            )
            delete_button.callback = self._delete_profile
            self.add_item(delete_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Uhe~... bảng cấu hình này của người khác mà Sensei.", ephemeral=True
            )
            return False
        return True

    async def _select_profile(self, interaction: discord.Interaction):
        self.selected_profile_id = interaction.data["values"][0]
        self._build_items()
        await interaction.response.edit_message(view=self)

    async def _edit_profile(self, interaction: discord.Interaction):
        profile = None
        if self.selected_profile_id:
            try:
                profile = await endpoint_profiles.get_profile(
                    self.owner_id, self.selected_profile_id
                )
            except endpoint_profiles.ProfileStoreError as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return
        await interaction.response.send_modal(
            EndpointProfileModal(self.owner_id, self, profile)
        )

    async def _delete_profile(self, interaction: discord.Interaction):
        if not self.selected_profile_id:
            await interaction.response.send_message(
                "Chọn profile trước đã nha.", ephemeral=True
            )
            return
        profile = next(
            (p for p in self.profiles if p["id"] == self.selected_profile_id), None
        )
        await interaction.response.send_message(
            f"Chắc chắn xóa profile **{profile['name'] if profile else 'đã chọn'}**?",
            view=DeleteProfileView(self, self.selected_profile_id, self.owner_id),
            ephemeral=True,
        )

    async def on_timeout(self):
        self.stop()


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

    if lowered == "config":
        try:
            profiles = await endpoint_profiles.list_profiles(message.author.id)
        except endpoint_profiles.ProfileStoreError as exc:
            await message.channel.send(str(exc))
            return True
        view = EndpointConfigView(message.author.id, profiles)
        view.config_message = await message.channel.send(
            embed=_config_embed(profiles, endpoint_profiles.encryption_configured()),
            view=view,
        )
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
        try:
            models = await model_settings.available_models(message.author.id)
            current_info = await model_settings.get_model_for_user(message.author.id)
        except endpoint_profiles.ProfileStoreError as exc:
            await message.channel.send(str(exc))
            return True
        if not models:
            await message.channel.send("Chưa có model nào được cấu hình.")
            return True
        current_name = current_info.get("name") or model_settings.name_for(current_info.get("id"))
        if current_name:
            current_line = f"Model hiện tại của bạn: **{current_name}**"
            if not current_info.get("multimodal", True):
                current_line += " (không xem được ảnh)"
        else:
            current_line = "Model hiện tại của bạn: không có trong MODEL_LIST"
        view = ModelSelectView(models, message.author.id, current_name)
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
        vec = d.get("vec") or {}
        dom = list(d.get("dominant") or [])
        mood_label = _MOOD_VI.get(d["label"], d["label"])
        blend = _vi_blend(vec, dom)
        if d["label"] != "default":
            mood_block = f"> {d['desc']}\n> *{blend}*"
        else:
            mood_block = f"> *{blend}*"
        parts = [
            f"🌸 **Cảm xúc** — {mood_label} ({d['valence']:+.1f})\n{mood_block}",
            f"💕 **Bond** — {bond_bar} {bond:.1f}/100\n> {bond_rank}",
        ]
        if d.get("why"):
            parts.append("☕ **Lý do:** " + "; ".join(_vi_why(w) for w in d["why"]))
        if vec:
            top = sorted(vec.items(), key=lambda kv: -kv[1])[:5]
            parts.append("📊 **Chi tiết:** " + " · ".join(
                f"{_EMO_VI.get(k, k)} {v * 100:.0f}%" for k, v in top))
        from affection import mood as mood_mod, relation
        if mood_mod.is_sleeping():
            parts.append("😴 Đang ngủ")
        rel_axes = relation.levels(message.author.id)
        if rel_axes:
            parts.append("↔️ **Quan hệ:** " + ", ".join(
                f"{_REL_AXIS_VI.get(a, a)} {_REL_WORD_VI.get(w, w)}" for a, w in rel_axes))
        aroused = await affection.check_aroused_state(message.author.id)
        if aroused:
            parts.append(f"🔥 **Aroused** — lv{aroused}")
        if is_dm:
            parts.append(f"🔥 **Lust** — {lust_bar} {lust:.1f}%")
        from affection import experience as experience_mgr, special
        if exp > 0:
            exp_label, exp_desc = experience_mgr.get_experience_label(exp)
            parts.append(f"✨ **Kỷ niệm vặt** — {exp:.1f} · {exp_label}: {exp_desc}")
        rel = special.embed_line(message.author.id)
        if rel:
            parts.append(f"💞 **Quan hệ đặc biệt** — {rel.strip()}")
        await message.channel.send("\n".join(parts))
        return True

    return False
