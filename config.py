import json
import os
import re
from datetime import timedelta, timezone
from dotenv import load_dotenv

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(PROJECT_DIR, ".env"), encoding="utf-8-sig")


def _database_path(value: str) -> str:
    return value if os.path.isabs(value) else os.path.join(PROJECT_DIR, value)

LM_BASE_URL = os.environ.get("LM_BASE_URL", "https://localhost:1234/v1")
LM_API_KEY = os.environ.get("LM_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "google/gemma4-12b-it")
REFLECTION_MODEL_NAME = MODEL_NAME


def _parse_model_choices(raw: str) -> dict[str, str]:
    """Đọc MODEL_LIST (JSON {"tên": "id"}) từ env, bỏ qua entry không hợp lệ."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    choices: dict[str, str] = {}
    for name, model_id in data.items():
        name = str(name).strip()
        model_id = str(model_id).strip()
        if not name or not model_id:
            continue
        if len(model_id) > 100:   # Discord select option value limit
            continue
        choices[name[:100]] = model_id
    return choices


MODEL_CHOICES = _parse_model_choices(
    os.environ.get("MODEL_LIST") or os.environ.get("MODELS") or ""
)

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

try:
    STATUS_CHANNEL_ID = int(os.environ.get("STATUS_CHANNEL_ID", "0") or 0)
except ValueError:
    STATUS_CHANNEL_ID = 0   # 0 = tắt thông báo bật/tắt bot

JINA_API_KEY = ""

DB_PATH = _database_path("database/hoshino_bot.db")

HISTORY_LIMIT = 20
HISTORY_SCAN_LIMIT = 300
GLOBAL_HISTORY_KEEP = 60
GLOBAL_HISTORY_LIMIT = 30

REFLECTION_TIMEOUT_SECONDS = 120
REFLECTION_MAX_TOKENS = 8192
DAILY_SUMMARY_TIMEOUT_SECONDS = 90
DAILY_SUMMARY_MAX_TOKENS = 149

BOT_PREFIX = "!hoshino"
MAX_TOOL_ITER = 4
MAX_OUTPUT_TOKENS_DEFAULT = 2048
MAX_OUTPUT_TOKENS_LIMIT = 16384

CLEAR_MARKER = "\u200b\u200c[HOSHINO_RESET_MARK]\u200c\u200b"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".html", ".css",
    ".xml", ".ini", ".toml", ".sh", ".bat", ".sql",
}
TEXT_EXTRA_MIMES = {"application/json", "application/xml", "application/x-yaml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}

MAX_TEXT_FILE_CHARS = 4000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 4

THOUGHT_TAG_PAIRS = (
    ("<|channel>thought", "<channel|>"),
    ("<think>", "</think>"),
    ("<thought>", "</thought>"),
)

GMT7 = timezone(timedelta(hours=7))

LOG_DIR = "logs"
LOG_LEVEL = "INFO"
LOG_TO_CONSOLE = True

AFFECTION_DB_PATH = DB_PATH
AFFECTION_BASE_EXP_MIN = 0.8
AFFECTION_BASE_EXP_MAX = 1.2

AFFECTION_RANKS = (
    (0, 10, "Người lạ - Xã giao lịch sự, giữ khoảng cách", 1.00),
    (10, 25, "Quen biết - Lười biếng, trêu chọc xã giao", 0.75),
    (25, 45, "Bạn bớt lạ - Thoải mái rủ rê trốn việc cùng", 0.55),
    (45, 60, "Sensei đáng tin - Coi là người lớn dựa dẫm được, nhưng vẫn giấu quá khứ", 0.38),
    (60, 75, "Ranh giới mở lòng - Bắt đầu rạn nứt bức tường tâm lý, phụ thuộc ngầm", 0.25),
    (75, 90, "Điểm tựa quý giá - Mở lòng hoàn toàn.", 0.14),
    (90, 100, "Tri kỷ không thể thay thế - Được chữa lành tổn thương quá khứ, gắn bó tuyệt đối", 0.07),
    (100, 101, "Báu vật quan trọng nhất của Hoshino", 0.00),
)

AFFECTION_MOOD_LEVELS = (
    (-100, -60, "distressed", "Hoshino đang rất suy sụp, hoang mang hoặc bị tổn thương nặng."),
    (-60, -20, "unhappy", "Hoshino đang bực bội, dỗi hoặc khó chịu trong người."),
    (-20, 20, "neutral", "Hoshino đang thong thả, lười biếng đúng kiểu thường ngày."),
    (20, 60, "cheerful", "Hoshino đang vui vẻ, thoải mái và thích trêu đùa Sensei."),
    (60, 101, "happy", "Hoshino đang cực kỳ vui vẻ và tràn đầy năng lượng."),
)

AFFECTION_TICK_INTERVAL = 10.0
AFFECTION_DB_FLUSH_EVERY_N = 6
AFFECTION_TEMP_READ_EVERY_N = 5
AFFECTION_SLEEP_AFTER = 3600.0
AFFECTION_WAKE_DISPLAY_WINDOW = 30.0
AFFECTION_SLEEP_REGEN_PER_TICK = 1.0
AFFECTION_HAPPY_CAP = 40.0
AFFECTION_SHOCKED_DURATION_SECONDS = 60.0
AFFECTION_DRIFT_STD = 0.75
AFFECTION_DECAY_PER_TICK = 0.25
AFFECTION_TEMP_FREEZE = 38.0
AFFECTION_TEMP_FREEZE_DELTA = -0.6
AFFECTION_TEMP_COOL = 42.0
AFFECTION_TEMP_COOL_DELTA = 0.0
AFFECTION_TEMP_SWEET = 52.0
AFFECTION_TEMP_SWEET_DELTA = 0.3
AFFECTION_TEMP_WARM = 68.0
AFFECTION_TEMP_WARM_DELTA = -0.5
AFFECTION_TEMP_HOT = 78.0
AFFECTION_TEMP_HOT_DELTA = -1.0
AFFECTION_TEMP_OVER = 87.0
AFFECTION_TEMP_OVER_DELTA = -3.0
AFFECTION_AROUSED_RANDOM_LEVEL1_CHANCE = 0.02
AFFECTION_AROUSED_RANDOM_LEVEL2_CHANCE = 0.0005
AFFECTION_AROUSED_RANDOM_DURATION_SECONDS = 180
AFFECTION_AROUSED_MODEL_DURATION_SECONDS = 3600
AFFECTION_AROUSED_COOLDOWN_SECONDS = 300
AFFECTION_AROUSED_BOND_THRESHOLD = 75


# ── Affection system v2 (emotion vector + bond decay + special relationship) ──
# Ported from the arona project. These names are what affection/*.py import.

TIMEZONE = "Asia/Ho_Chi_Minh"   # daily bond cap and night-time fatigue

# Tick timing
TICK_INTERVAL = AFFECTION_TICK_INTERVAL
DB_FLUSH_EVERY_N = AFFECTION_DB_FLUSH_EVERY_N
TEMP_READ_EVERY_N = AFFECTION_TEMP_READ_EVERY_N

# Idle / sleep thresholds (seconds)
SLEEP_AFTER = AFFECTION_SLEEP_AFTER
WAKE_DISPLAY_WINDOW = AFFECTION_WAKE_DISPLAY_WINDOW

# CPU temperature thresholds (aliases to the legacy AFFECTION_* values)
TEMP_FREEZE = AFFECTION_TEMP_FREEZE
TEMP_FREEZE_DELTA = AFFECTION_TEMP_FREEZE_DELTA
TEMP_COOL = AFFECTION_TEMP_COOL
TEMP_COOL_DELTA = AFFECTION_TEMP_COOL_DELTA
TEMP_SWEET = AFFECTION_TEMP_SWEET
TEMP_SWEET_DELTA = AFFECTION_TEMP_SWEET_DELTA
TEMP_WARM = AFFECTION_TEMP_WARM
TEMP_WARM_DELTA = AFFECTION_TEMP_WARM_DELTA
TEMP_HOT = AFFECTION_TEMP_HOT
TEMP_HOT_DELTA = AFFECTION_TEMP_HOT_DELTA
TEMP_OVER = AFFECTION_TEMP_OVER
TEMP_OVER_DELTA = AFFECTION_TEMP_OVER_DELTA

# Bond ranks + exp (aliases kept for affection/bond.py)
RANKS = AFFECTION_RANKS
BASE_EXP_MIN = AFFECTION_BASE_EXP_MIN
BASE_EXP_MAX = AFFECTION_BASE_EXP_MAX

# Bond idle decay: each whole day beyond the grace period costs BOND_DECAY_PER_DAY,
# never dropping below the lower bound of the user's current rank.
BOND_DECAY_GRACE_DAYS = 7.0
BOND_DECAY_PER_DAY = 0.5
BOND_DECAY_CHECK_SECS = 3600.0

# Model-driven bond change via <bond>N</bond>
BOND_TAG_MIN = -8.0
BOND_TAG_MAX = 5.0
BOND_TAG_COOLDOWN = 300.0   # seconds between applied NEGATIVE tags per user
BOND_TAG_DAILY_CAP = 15.0   # max bond gained per user per day through positive tags

# Special relationship (saved-info line `Relationship: label | description`)
SPECIAL_MIN_BOND = 25.0
SPECIAL_STRAINED_BOND = 15.0
SPECIAL_LABEL_MAX = 30
SPECIAL_DESC_MAX = 80
SPECIAL_DECAY_MULT = 0.5
SPECIAL_LEAK_MULT = 1.5
SPECIAL_WARMTH_BONUS = 0.05

# Emotion vector
EMOTIONS = ("joy", "warmth", "excitement", "sadness", "anger", "anxiety", "surprise", "fatigue", "curiosity")
POSITIVE_EMOS = ("joy", "warmth", "excitement", "curiosity")
NEGATIVE_EMOS = ("sadness", "anger", "anxiety")

EMO_BASELINE = {
    "joy": 0.25, "warmth": 0.30, "excitement": 0.10, "sadness": 0.05, "anger": 0.02,
    "anxiety": 0.05, "surprise": 0.0, "fatigue": 0.05, "curiosity": 0.20,
}
EMO_HALFLIFE = {
    "joy": 40 * 60, "warmth": 90 * 60, "excitement": 20 * 60, "sadness": 120 * 60, "anger": 60 * 60,
    "anxiety": 45 * 60, "surprise": 60, "fatigue": 180 * 60, "curiosity": 30 * 60,
}
VALENCE_WEIGHTS = {
    "joy": 1.0, "warmth": 0.7, "excitement": 0.5, "curiosity": 0.2,
    "sadness": -1.0, "anger": -1.0, "anxiety": -0.5, "fatigue": -0.2, "surprise": 0.0,
}

EMO_INTERACTIONS = [
    ("anger", "joy", -0.004), ("anger", "warmth", -0.002), ("anger", "curiosity", -0.002),
    ("warmth", "anger", -0.003),
    ("joy", "sadness", -0.003), ("joy", "anxiety", -0.001), ("joy", "curiosity", +0.001),
    ("sadness", "joy", -0.0015), ("sadness", "anxiety", +0.002),
    ("anxiety", "curiosity", -0.002),
    ("excitement", "curiosity", +0.001),
    ("fatigue", "excitement", -0.003), ("fatigue", "curiosity", -0.002), ("fatigue", "joy", -0.0015),
]

TEMP_ANXIETY_COEF = 0.0005
TEMP_ANGER_COEF = 0.0004
TEMP_WARMTH_COEF = 0.0004

# Tags: <mood>joy+8 anxiety+3 | reason</mood> (1 unit = 0.01 intensity)
EMO_UNIT = 0.01
EMO_TAG_MAX = 15
EMO_TAG_MAX_COUNT = 3
LEGACY_TAG_SCALE = 0.15 / 30.0
TAG_DEDUPE_WINDOW = 120.0

OFFLINE_DECAY_MULT = {"anger": 0.4, "anxiety": 0.4, "sadness": 0.7}

USER_OFFSET_MAX = 0.6
USER_HALFLIFE = {
    "joy": 90 * 60, "warmth": 180 * 60, "excitement": 60 * 60, "sadness": 150 * 60, "anger": 120 * 60,
    "anxiety": 90 * 60, "surprise": 120, "curiosity": 60 * 60,
}
USER_IDLE_PURGE = 24 * 3600.0
LEAK_POSITIVE = 0.25
LEAK_NEGATIVE = 0.10
LEAK_HOURLY_CAP = 0.10
BOND_FORGIVE_DIV = 250.0
BOND_FORGIVE_MAX = 0.4
BOND_WARMTH_PER_POINT = 0.002
BOND_WARMTH_MAX = 0.2
WARMTH_FLOOR_PER_BOND = 0.001

COLLAPSE_MIN = 30.0
COLLAPSE_MAX = 60.0
COLLAPSE_TEMP = 0.15
COLLAPSE_SECOND_MIN = 0.12
COLLAPSE_SECOND_RATIO = 0.5
COLLAPSE_OVERRIDE_DELTA = 0.12
COLLAPSE_OVERRIDE_SURPRISE = 0.4
COLLAPSE_OVERRIDE_MARGIN = 0.2
COLLAPSE_OVERRIDE_MIN_GAP = 10.0

CAUSE_REASON_MAX = 60
CAUSE_MIN_DELTA = 0.03
CAUSE_SHOW_MIN = 0.05
CAUSE_PRUNE_MIN = 0.01
CAUSE_MAX_AGE = 48 * 3600.0
CAUSE_MAX_PER_EMO = 3
CAUSE_TOTAL_MAX = 200

FATIGUE_RISE_HALFLIFE = 45 * 60
FATIGUE_SLEEP_HALFLIFE = 30 * 60
FATIGUE_ACTIVITY_WINDOW = 1800.0
FATIGUE_ACTIVITY_FULL = 60
FATIGUE_ACTIVITY_MAX = 0.30
FATIGUE_NIGHT_BONUS = 0.30
FATIGUE_LATE_BONUS = 0.10
FATIGUE_WAKE_BUMP = 0.15
SLEEP_JOY_REGEN_PER_TICK = 0.001
SLEEP_JOY_CAP = 0.45

LABEL_THRESHOLDS = {"surprise": 0.30, "anger": 0.12, "sadness": 0.12, "excitement": 0.15, "joy": 0.12, "warmth": 0.10}
LABEL_DELIGHTED_JOY = 0.65
EMO_DRIFT_STD = 0.002

MOOD_LABEL_DESC = {
    "default": "Hoshino đang ở trạng thái thường ngày, thong thả và lười biếng đúng kiểu.",
    "happy": "Hoshino đang vui vẻ, ấm áp và dễ gần.",
    "delighted": "Hoshino đang cực kỳ vui, tíu tít và tinh nghịch.",
    "motivated": "Hoshino đang tràn đầy năng lượng và hào hứng.",
    "sad": "Hoshino đang buồn và hơi thu mình lại.",
    "angered": "Hoshino đang thực sự giận, lạnh nhạt và khó chịu.",
    "shocked": "Hoshino đang sốc, hoàn toàn bất ngờ.",
}
