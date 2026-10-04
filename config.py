import os
import re
from datetime import timedelta, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

def _database_path(value: str) -> str:
    return value if os.path.isabs(value) else os.path.join(PROJECT_DIR, value)

LM_BASE_URL = os.environ.get("LM_BASE_URL", "https://imgxh.eu.org/v1")
LM_API_KEY = os.environ.get("LM_API_KEY", "")
MODEL_NAME = os.environ.get("MODEL_NAME", "imgxh/roleplay")
REFLECTION_MODEL_NAME = MODEL_NAME
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

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

THINK_TAG_RE = re.compile(
    "|".join(
        f"{re.escape(opening)}(.*?){re.escape(closing)}"
        for opening, closing in THOUGHT_TAG_PAIRS
    ),
    re.DOTALL | re.IGNORECASE,
)

_LOWER_TAG_LITERALS = tuple(
    tag.lower() for pair in THOUGHT_TAG_PAIRS for tag in pair
)
_MAX_TAG_LEN = max(len(tag) for tag in _LOWER_TAG_LITERALS)


def scan_thought(text: str) -> tuple[str, list[str]]:
    """Tách text thành (phần hội thoại, các khối suy luận).

    Mọi khối suy luận — kể cả khối chưa đóng thẻ — đều bị lấy khỏi phần hội
    thoại, để thẻ reasoning không bao giờ lọt ra Discord."""
    lowered = text.lower()
    visible: list[str] = []
    thoughts: list[str] = []
    index = 0
    length = len(text)

    while index < length:
        next_open = lowered.find("<", index)
        if next_open == -1:
            visible.append(text[index:])
            break
        if next_open > index:
            visible.append(text[index:next_open])
            index = next_open

        for opening, closing in THOUGHT_TAG_PAIRS:
            if not lowered.startswith(opening, index):
                continue
            body_start = index + len(opening)
            body_end = lowered.find(closing, body_start)
            body = text[body_start:body_end if body_end != -1 else length].strip()
            if body:
                thoughts.append(body)
            if body_end == -1:
                return "".join(visible), thoughts
            index = body_end + len(closing)
            break
        else:
            visible.append("<")
            index += 1

    return "".join(visible), thoughts


def cut_partial_tag(text: str) -> str:
    """Cắt phần đuôi text có thể là một thẻ thought chưa gửi hết, để chờ delta
    tiếp theo thay vì lộ thẻ ra giữa chừng."""
    for index in range(max(0, len(text) - _MAX_TAG_LEN), len(text)):
        if text[index] != "<":
            continue
        tail = text[index:].lower()
        if any(literal.startswith(tail) for literal in _LOWER_TAG_LITERALS):
            return text[:index]
    return text

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
