"""Per-user model selection, persisted in the user_settings DB table."""

import hashlib

import config
import endpoint_profiles
from client import db
from logger import logger

SETTING_KEY = "model_name"
_user_models: dict[int, dict] = {}


async def available_models(user_id: int) -> dict[str, dict]:
    """Return configured models plus the user's decrypted custom endpoint models."""
    choices = {name: dict(info) for name, info in config.MODEL_CHOICES.items()}
    for profile in await endpoint_profiles.list_profiles(user_id):
        overrides = profile.get("overrides") or {}
        for model_id in profile["models"]:
            name = f"{profile['name']} / {model_id}"
            if len(name) > 100:
                suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
                name = f"{name[:90]} [{suffix}]"
            while name in choices:
                name = f"{name[:90]} [{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}]"

            caps = endpoint_profiles.detect_capabilities(model_id)
            caps.update(endpoint_profiles.normalize_override(overrides.get(model_id)))
            capability = endpoint_profiles.capability_label(caps)
            choices[name] = {
                "id": model_id,
                "multimodal": caps["image"],
                "image": caps["image"],
                "audio": caps["audio"],
                "video": caps["video"],
                "tool": caps.get("tool", True),
                "description": f"[{profile['name']}] {capability} · sửa: !hoshino config"[:100],
                "profile_id": profile["id"],
                "base_url": profile["base_url"],
            }
    return choices


def name_for(model_id: str) -> str | None:
    for name, info in config.MODEL_CHOICES.items():
        if info.get("id") == model_id:
            return name
    return None


def _named(name: str | None, info: dict) -> dict:
    named = dict(info)
    named["name"] = name
    return named


def _default_info() -> dict:
    for name, info in config.MODEL_CHOICES.items():
        if info.get("id") == config.MODEL_NAME:
            return _named(name, info)
    # MODEL_NAME mặc định có thể không nằm trong MODEL_LIST; giữ hành vi cũ là nhận ảnh.
    return _named(None, {
        "id": config.MODEL_NAME,
        "multimodal": True,
        "image": True,
        "audio": False,
        "video": False,
        "tool": True,
        "description": "",
    })


def invalidate_user_cache(user_id: int) -> None:
    _user_models.pop(int(user_id), None)


async def get_model_for_user(user_id: int) -> dict:
    """Model info this user selected, falling back to MODEL_NAME from .env."""
    user_id = int(user_id)
    if user_id in _user_models:
        return dict(_user_models[user_id])

    try:
        saved = await db.get_user_setting(user_id, SETTING_KEY)
    except Exception:
        logger.exception("Không đọc được model đã lưu của user=%s", user_id)
        saved = None

    choices = await available_models(user_id)
    if saved in choices:
        info = _named(saved, choices[saved])
    else:
        # Tương thích dữ liệu cũ: trước đây setting lưu model id thay vì tên.
        legacy = next(
            ((name, item) for name, item in choices.items() if item.get("id") == saved),
            None,
        )
        info = _named(*legacy) if legacy else _default_info()

    _user_models[user_id] = info
    return dict(info)


async def set_model_for_user(user_id: int, model_name: str) -> str | None:
    """Save a model choice (display name) for one user. Returns the name, or None if invalid."""
    choices = await available_models(user_id)
    info = choices.get(model_name)
    if info is None:
        return None

    user_id = int(user_id)
    await db.set_user_setting(user_id, SETTING_KEY, model_name)
    _user_models[user_id] = _named(model_name, info)
    logger.info("Đã đổi model của user=%s -> %s (%s)", user_id, model_name, info.get("id"))
    return model_name
