"""Footer thong ke tang/giam gan vao reply, va chuyen nguoc lai thanh tag khi doc history."""
import re

MOOD_EMOJI = {
    "joy": "😊", "warmth": "🫶", "excitement": "✨", "sadness": "😢", "anger": "😠",
    "anxiety": "😟", "surprise": "😮", "fatigue": "😴", "curiosity": "🤔",
}
REL_EMOJI = {
    "trust": "🤝", "respect": "🫡", "tension": "⚡", "playfulness": "😜", "ease": "😌", "openness": "🫂",
}
PREFIX = "-# "
_SHOCKED_RE = re.compile(r"^😱\s+Shocked:\s*(?P<reason>.+)$")
_SHOCKED_MAX = 120
FOOTER_RE = re.compile(r"^-# (?P<body>[^\n]*)$", re.MULTILINE)
_ITEM_RE = re.compile(r"^(?P<emoji>\S+)\s+(?P<name>.+?)\s+(?P<val>[+-]?\d+(?:\.\d+)?)$")


def _fmt(value):
    return f"{value:+g}"


def build(stats):
    if not stats:
        return ""
    parts = []
    if stats.get("bond"):
        parts.append(f"❤️ Bond {_fmt(stats['bond'])}")
    shocked = (stats.get("shocked") or "").strip()
    for key, val in (stats.get("mood") or {}).items():
        if key == "surprise" and shocked:
            continue
        if val:
            parts.append(f"{MOOD_EMOJI.get(key, '•')} {key.capitalize()} {_fmt(val)}")
    for key, val in (stats.get("rel") or {}).items():
        if val:
            parts.append(f"{REL_EMOJI.get(key, '•')} {key.capitalize()} {_fmt(val)}")
    if stats.get("arousal"):
        parts.append(f"🥵 Arousal {_fmt(stats['arousal'])}")
    if stats.get("lust"):
        parts.append(f"🔥 Lust lv. {int(stats['lust']):+d}")
    if shocked:
        parts.append(f"😱 Shocked: {shocked}")
    return PREFIX + " · ".join(parts) if parts else ""


def _sanitize_reason(text):
    return " ".join(str(text).replace("<", "").replace(">", "").split())[:_SHOCKED_MAX]


def _parse_body(body):
    bond, arousal, lust = 0.0, 0.0, 0
    mood, rel, shocked = {}, {}, ""
    for part in body.split(" · "):
        shocked_match = _SHOCKED_RE.match(part.strip())
        if shocked_match:
            shocked = _sanitize_reason(shocked_match.group("reason"))
            continue
        match = _ITEM_RE.match(part.strip())
        if not match:
            return None
        name = match.group("name").strip().lower()
        value = float(match.group("val"))
        if name == "bond":
            bond = value
        elif name == "arousal":
            arousal = value
        elif name.startswith("lust"):
            lust = int(value)
        elif name in MOOD_EMOJI:
            mood[name] = value
        elif name in REL_EMOJI:
            rel[name] = value
        else:
            return None
    tags = ""
    if bond:
        tags += f"<bond>{_fmt(bond)}</bond>"
    if mood:
        tags += "<mood>" + " ".join(f"{k}{_fmt(v)}" for k, v in mood.items()) + "</mood>"
    if rel:
        tags += "<rel>" + " ".join(f"{k}{_fmt(v)}" for k, v in rel.items()) + "</rel>"
    if arousal:
        tags += f'<arousal_change amount="{_fmt(arousal)}"/>'
    if lust:
        tags += f'<lust_change amount="{lust:+d}"/>'
    if shocked:
        tags += f"<shocked>{shocked}</shocked>"
    return tags


def footer_to_tags(text):
    def _repl(match):
        tags = _parse_body(match.group("body"))
        return tags if tags else match.group(0)

    return FOOTER_RE.sub(_repl, text)
