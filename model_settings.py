"""Per-user model selection, persisted in the user_settings DB table."""

import config
from client import db
from logger import logger

SETTING_KEY = "model_name"
_user_models: dict[int, dict] = {}


def available_models() -> dict[str, dict]:
    """Mapping of display name -> {"id", "multimodal", "description"}, from MODEL_LIST."""
    return config.MODEL_CHOICES


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
    return _named(None, {"id": config.MODEL_NAME, "multimodal": True, "description": ""})


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

    if saved in config.MODEL_CHOICES:
        info = _named(saved, config.MODEL_CHOICES[saved])
    else:
        # Tương thích dữ liệu cũ: trước đây setting lưu model id thay vì tên.
        legacy = next(
            ((name, i) for name, i in config.MODEL_CHOICES.items() if i.get("id") == saved),
            None,
        )
        info = _named(*legacy) if legacy else _default_info()

    _user_models[user_id] = info
    return dict(info)


async def set_model_for_user(user_id: int, model_name: str) -> str | None:
    """Save a model choice (display name) for one user. Returns the name, or None if invalid."""
    info = config.MODEL_CHOICES.get(model_name)
    if info is None:
        return None

    user_id = int(user_id)
    await db.set_user_setting(user_id, SETTING_KEY, model_name)
    _user_models[user_id] = _named(model_name, info)
    logger.info("Đã đổi model của user=%s -> %s (%s)", user_id, model_name, info.get("id"))
    return model_name
