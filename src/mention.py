from __future__ import annotations
import re

def parse_mentions(text: str, known_participants: list[str]) -> list[str]:
    known_lower = {p.lower() for p in known_participants}
    mentions: list[str] = []
    for match in re.finditer(r"(?:^|\s)@([a-zA-Z][a-zA-Z0-9_-]*)\b", text):
        name = match.group(1).lower()
        if name in known_lower and name not in mentions:
            mentions.append(name)
    return mentions
