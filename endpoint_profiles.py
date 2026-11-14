"""Encrypted per-user OpenAI-compatible endpoint profiles."""

import asyncio
import base64
import hashlib
import json
import re
import uuid
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from openai import AsyncOpenAI

import config
from client import db

SETTING_KEY = "custom_endpoint_profiles"
_MULTIMODAL_PATTERNS = (
    r"vision",
    r"\bvl\b",
    r"llava",
    r"pixtral",
    r"omni",
    r"gpt-4o",
    r"gpt-4\.1",
    r"gpt-4-turbo",
    r"claude-3",
    r"claude-sonnet-4",
    r"gemini",
    r"qwen\d?(?:\.\d+)?-vl",
    r"internvl",
    r"minicpm-v",
    r"molmo",
    r"phi-4-multimodal",
)
_user_locks: dict[int, asyncio.Lock] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    return _user_locks.setdefault(int(user_id), asyncio.Lock())


class ProfileStoreError(Exception):
    """A safe-to-display configuration or storage error."""


def _cipher() -> Fernet:
    secret = str(config.CUSTOM_ENDPOINT_ENCRYPTION_KEY or "")
    if len(secret) < 32:
        raise ProfileStoreError(
            "Chưa cấu hình CUSTOM_ENDPOINT_ENCRYPTION_KEY (cần secret dài ít nhất 32 ký tự) trong .env."
        )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encryption_configured() -> bool:
    try:
        _cipher()
        return True
    except ProfileStoreError:
        return False


async def _read_profiles(user_id: int) -> list[dict]:
    encrypted = await db.get_user_setting(int(user_id), SETTING_KEY)
    if not encrypted:
        return []
    try:
        payload = _cipher().decrypt(encrypted.encode("ascii"))
        profiles = json.loads(payload.decode("utf-8"))
    except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
        raise ProfileStoreError(
            "Không giải mã được profile. Kiểm tra CUSTOM_ENDPOINT_ENCRYPTION_KEY trong .env."
        ) from exc
    if not isinstance(profiles, list):
        raise ProfileStoreError("Dữ liệu profile trong DB không hợp lệ.")
    return profiles


async def _write_profiles(user_id: int, profiles: list[dict]) -> None:
    payload = json.dumps(profiles, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = _cipher().encrypt(payload).decode("ascii")
    await db.set_user_setting(int(user_id), SETTING_KEY, encrypted)


async def list_profiles(user_id: int) -> list[dict]:
    return [
        {
            "id": profile["id"],
            "name": profile["name"],
            "base_url": profile["base_url"],
            "models": list(profile.get("models") or []),
            "overrides": dict(profile.get("overrides") or {}),
        }
        for profile in await _read_profiles(user_id)
    ]


async def get_profile(user_id: int, profile_id: str) -> dict:
    profile = next(
        (item for item in await _read_profiles(user_id) if item.get("id") == profile_id),
        None,
    )
    if profile is None:
        raise ProfileStoreError("Không tìm thấy profile endpoint này.")
    return profile


def detect_multimodal(model_id: str) -> bool:
    lowered = model_id.lower().replace("_", "-")
    return any(re.search(pattern, lowered) for pattern in _MULTIMODAL_PATTERNS)


def parse_overrides(raw: str) -> dict[str, bool | None]:
    overrides = {}
    for item in re.split(r"[\n,]+", raw or ""):
        item = item.strip()
        if not item:
            continue
        model_id, separator, value = item.rpartition("=")
        normalized = value.strip().lower()
        if not separator or normalized not in {"true", "false", "auto"}:
            raise ProfileStoreError(
                "Override cần theo dạng `model-id=true`, `model-id=false` hoặc `model-id=auto`, mỗi model một dòng."
            )
        model_id = model_id.strip()
        if not model_id:
            raise ProfileStoreError("Override multimodal đang thiếu model ID.")
        overrides[model_id] = (
            None if normalized == "auto" else normalized == "true"
        )
    return overrides


def _validate_endpoint(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ProfileStoreError("Endpoint cần là URL http(s) hợp lệ, ví dụ `https://host/v1`.")
    return base_url


def _parse_api_keys(raw: str) -> list[str]:
    keys = [key.strip() for key in (raw or "").split(",") if key.strip()]
    if not keys:
        raise ProfileStoreError("Cần nhập ít nhất một API key.")
    if any("\n" in key or "\r" in key for key in keys):
        raise ProfileStoreError("API key không được chứa xuống dòng.")
    return list(dict.fromkeys(keys))


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def is_retryable_provider_error(error: Exception) -> bool:
    status = _status_code(error)
    return status in {408, 409, 429} or (status is not None and 500 <= status <= 599)


async def _discover_models(base_url: str, api_keys: list[str], start_index: int):
    last_error = None
    for attempt in range(len(api_keys)):
        key_index = (start_index + attempt) % len(api_keys)
        client = AsyncOpenAI(base_url=base_url, api_key=api_keys[key_index])
        try:
            result = await client.models.list()
            model_ids = list(dict.fromkeys(
                str(getattr(item, "id", "")).strip()
                for item in result.data
                if getattr(item, "id", None)
            ))
            if not model_ids:
                raise ProfileStoreError("Endpoint kết nối được nhưng không trả model nào từ `/models`.")
            return model_ids, key_index
        except ProfileStoreError:
            raise
        except Exception as exc:
            last_error = exc
            if not is_retryable_provider_error(exc) or attempt + 1 >= len(api_keys):
                raise ProfileStoreError(
                    "Không lấy được danh sách model. Kiểm tra endpoint, API key và quyền truy cập `/models`."
                ) from exc
        finally:
            await client.close()
    raise ProfileStoreError("Không lấy được danh sách model từ endpoint.") from last_error


async def _save_profile(
    user_id: int,
    profile_id: str | None,
    name: str,
    base_url: str,
    api_keys_raw: str,
    overrides_raw: str,
) -> tuple[dict, int]:
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise ProfileStoreError("Tên profile cần dài từ 1 đến 40 ký tự.")
    base_url = _validate_endpoint(base_url)
    overrides = parse_overrides(overrides_raw)
    _cipher()

    profiles = await _read_profiles(user_id)
    old_profile = next((p for p in profiles if p.get("id") == profile_id), None)
    if profile_id and old_profile is None:
        raise ProfileStoreError("Profile đã chọn không còn tồn tại, hãy mở lại !hoshino config.")
    if any(
        p.get("id") != profile_id and p.get("name", "").casefold() == name.casefold()
        for p in profiles
    ):
        raise ProfileStoreError("Tên profile đã được dùng; hãy chọn tên khác.")
    if not old_profile and len(profiles) >= 25:
        raise ProfileStoreError("Đã đạt giới hạn 25 profile cho mỗi user.")

    if api_keys_raw.strip():
        api_keys = _parse_api_keys(api_keys_raw)
        key_index = 0
    elif old_profile:
        api_keys = list(old_profile.get("api_keys") or [])
        key_index = int(old_profile.get("key_index", 0) or 0) % len(api_keys)
    else:
        raise ProfileStoreError("Cần nhập ít nhất một API key.")

    model_ids, key_index = await _discover_models(base_url, api_keys, key_index)
    existing_overrides = dict(old_profile.get("overrides") or {}) if old_profile else {}
    existing_overrides.update(overrides)
    updated = {
        "id": profile_id or uuid.uuid4().hex,
        "name": name,
        "base_url": base_url,
        "api_keys": api_keys,
        "key_index": key_index,
        "models": model_ids,
        "overrides": {
            model_id: multimodal
            for model_id, multimodal in existing_overrides.items()
            if model_id in model_ids and multimodal is not None
        },
    }

    if old_profile:
        profiles[profiles.index(old_profile)] = updated
    else:
        profiles.append(updated)
    await _write_profiles(user_id, profiles)
    return {
        "id": updated["id"],
        "name": updated["name"],
        "base_url": updated["base_url"],
        "models": list(updated["models"]),
    }, len(model_ids)


async def save_profile(
    user_id: int,
    profile_id: str | None,
    name: str,
    base_url: str,
    api_keys_raw: str,
    overrides_raw: str,
) -> tuple[dict, int]:
    async with _user_lock(user_id):
        return await _save_profile(
            user_id, profile_id, name, base_url, api_keys_raw, overrides_raw
        )


async def _delete_profile(user_id: int, profile_id: str) -> str:
    profiles = await _read_profiles(user_id)
    remaining = [p for p in profiles if p.get("id") != profile_id]
    if len(remaining) == len(profiles):
        raise ProfileStoreError("Profile đã chọn không còn tồn tại.")
    removed = next(p for p in profiles if p.get("id") == profile_id)
    await _write_profiles(user_id, remaining)
    return removed["name"]


async def delete_profile(user_id: int, profile_id: str) -> str:
    async with _user_lock(user_id):
        return await _delete_profile(user_id, profile_id)


async def get_profile_auth(user_id: int, profile_id: str) -> dict:
    profile = await get_profile(user_id, profile_id)
    keys = list(profile.get("api_keys") or [])
    if not keys:
        raise ProfileStoreError("Profile không có API key.")
    index = int(profile.get("key_index", 0) or 0) % len(keys)
    return {
        "base_url": profile["base_url"],
        "api_key": keys[index],
        "key_index": index,
        "key_count": len(keys),
    }


async def _advance_profile_key(user_id: int, profile_id: str) -> dict:
    profiles = await _read_profiles(user_id)
    profile = next((p for p in profiles if p.get("id") == profile_id), None)
    if profile is None:
        raise ProfileStoreError("Profile endpoint này không còn tồn tại.")
    keys = list(profile.get("api_keys") or [])
    if not keys:
        raise ProfileStoreError("Profile không có API key.")
    index = (int(profile.get("key_index", 0) or 0) + 1) % len(keys)
    profile["key_index"] = index
    await _write_profiles(user_id, profiles)
    return {
        "base_url": profile["base_url"],
        "api_key": keys[index],
        "key_index": index,
        "key_count": len(keys),
    }


async def advance_profile_key(user_id: int, profile_id: str) -> dict:
    async with _user_lock(user_id):
        return await _advance_profile_key(user_id, profile_id)
