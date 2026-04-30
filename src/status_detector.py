from __future__ import annotations
import json
import re
from difflib import SequenceMatcher

def parse_status_block(text: str) -> dict | None:
    pattern = r"<<<ROUNDTABLE_STATUS>>>\s*\n(.*?)\n\s*<<<END_STATUS>>>"
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].group(1).strip())
        if "status" in data:
            if len(matches) > 1:
                data["warning"] = "multiple_status_blocks"
                data["status_block_count"] = len(matches)
            return data
        return None
    except (json.JSONDecodeError, KeyError):
        return None

def extract_content_without_status_blocks(text: str) -> str:
    pattern = r"<<<ROUNDTABLE_STATUS>>>\s*\n.*?\n\s*<<<END_STATUS>>>"
    return re.sub(pattern, "", text, flags=re.DOTALL).strip()

def heuristic_detect(
    prev_same: str | None,
    curr_same: str | None,
    response_a: str | None,
    response_b: str | None,
) -> str:
    if prev_same and curr_same:
        ratio = SequenceMatcher(None, prev_same.lower(), curr_same.lower()).ratio()
        if ratio > 0.8:
            return "stalemate"
    if response_a and response_b:
        ratio = SequenceMatcher(None, response_a.lower(), response_b.lower()).ratio()
        if ratio > 0.6:
            return "converging"
    return "unknown"
