import re


_H_PREFERENCE_RE = re.compile(
    r"^\*{0,2}H-Preference\*{0,2}(?:\s*[—:-]\s*|\s+)(.*)$",
    re.IGNORECASE,
)
_BASE_SECTIONS = ("Pacing", "Language", "Tone")


def _ensure_h_preference_null(content: str) -> str:
    """Bảo đảm guide có dòng H-Preference (nullable).

    Nếu model chính từ chối / bỏ trống phần H, tự điền `H-Preference — null`
    thay vì để format hỏng. Chỉ can thiệp khi 3 section gốc đã có đủ.
    """
    lines = [line.strip().strip("`") for line in (content or "").splitlines() if line.strip()]
    if not lines or lines == ["-"]:
        return "\n".join(lines)

    base_patterns = [
        re.compile(rf"^\*{{0,2}}{re.escape(name)}\*{{0,2}}(?:\s|—|:)", re.IGNORECASE)
        for name in _BASE_SECTIONS
    ]
    if not all(any(pattern.match(line) for line in lines) for pattern in base_patterns):
        return "\n".join(lines)

    for index, line in enumerate(lines):
        match = _H_PREFERENCE_RE.match(line)
        if match:
            if not match.group(1).strip().strip("`").strip():
                lines[index] = "H-Preference — null"
            return "\n".join(lines)

    lines.append("H-Preference — null")
    return "\n".join(lines)


def project_personalization_guide(content: str, allow_h_preference: bool = False) -> str:
    """Return only sections allowed for the current conversation context."""
    allowed = {"Pacing", "Language", "Tone"}
    if allow_h_preference:
        allowed.add("H-Preference")
    lines = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip().strip("`")
        if not line:
            continue
        section_match = re.match(r"^\**([A-Za-z-]+)\**(?:\s*[—:-]|\s+)", line)
        if section_match and section_match.group(1) in allowed:
            lines.append(line)
    return "\n".join(lines)


def validate_personalization_guide(content: str, allow_h_preference: bool = False) -> str | None:
    if allow_h_preference:
        content = _ensure_h_preference_null(content)
    lines = [line.strip().strip("`") for line in (content or "").splitlines() if line.strip()]
    expected = ("Pacing", "Language", "Tone", "H-Preference") if allow_h_preference else (
        "Pacing", "Language", "Tone"
    )
    if len(lines) != len(expected):
        return None

    section_indexes = []
    for section_name in expected:
        matches = [
            index for index, line in enumerate(lines)
            if re.match(rf"^\*{{0,2}}{re.escape(section_name)}\*{{0,2}}(?:\s|—|:)", line)
        ]
        if len(matches) != 1:
            return None
        section_indexes.append(matches[0])

    if section_indexes != sorted(section_indexes):
        return None
    return "\n".join(lines)
