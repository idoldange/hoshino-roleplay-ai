"""Per-user model selection, persisted in the user_settings DB table."""

import config
from client import db
from logger import logger

SETTING_KEY = "model_name"
_user_models: dict[int, str] = {}


def available_models() -> dict[str, str]:
    """Mapping of display name -> model id, from MODEL_LIST in .env."""
    return config.MODEL_CHOICES


def name_for(model_id: str) -> str | None:
    for name, choice_id in config.MODEL_CHOICES.items():
        if choice_id == model_id:
            return name
    return None


async def get_model_for_user(user_id: int) -> str:
    """Model id this user selected, falling back to MODEL_NAME from .env."""
    user_id = int(user_id)
    if user_id in _user_models:
        return _user_models[user_id]

    try:
        saved = await db.get_user_setting(user_id, SETTING_KEY)
    except Exception:
        logger.exception("Không đọc được model đã lưu của user=%s", user_id)
        saved = None

    model_id = saved if saved in config.MODEL_CHOICES.values() else config.MODEL_NAME
    _user_models[user_id] = model_id
    return model_id


async def set_model_for_user(user_id: int, model_id: str) -> str | None:
    """Save a model choice for one user. Returns its display name, or None if invalid."""
    if model_id not in config.MODEL_CHOICES.values():
        return None

    user_id = int(user_id)
    await db.set_user_setting(user_id, SETTING_KEY, model_id)
    _user_models[user_id] = model_id
    name = name_for(model_id) or model_id
    logger.info("Đã đổi model của user=%s -> %s (%s)", user_id, name, model_id)
    return name
