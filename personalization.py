import re


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
