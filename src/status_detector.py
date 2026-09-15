from __future__ import annotations
import json
import re
from difflib import SequenceMatcher

VALID_STATUSES = frozenset({"converging", "diverging", "stalemate"})


def parse_status_block(text: str) -> dict | None:
    pattern = r"<<<ROUNDTABLE_STATUS>>>\s*\n(.*?)\n\s*<<<END_STATUS>>>"
    matches = list(re.finditer(pattern, text, re.DOTALL))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].group(1).strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    if status not in VALID_STATUSES:
        return None
    summary = data.get("summary")
    if summary is not None and not isinstance(summary, str):
        return None
    if len(matches) > 1:
        data["warning"] = "multiple_status_blocks"
        data["status_block_count"] = len(matches)
    return data

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


def heuristic_detect_many(previous: list[str | None], responses: list[str | None]) -> str:
    """Aggregate heuristic signals across every available participant.

    The legacy two-response helper remains public for compatibility; this
    variant prevents multi-participant rounds from being decided by list order.
    """
    paired = [
        (previous[index] if index < len(previous) else None, response)
        for index, response in enumerate(responses)
        if response
    ]
    usable = [response for _, response in paired]
    if len(usable) < 2:
        return "unknown"
    stalemate_votes = 0
    convergence_votes = 0
    for previous_response, response in paired:
        if previous_response and heuristic_detect(previous_response, response, None, None) == "stalemate":
            stalemate_votes += 1
    for index, first in enumerate(usable):
        for second in usable[index + 1 :]:
            if heuristic_detect(None, None, first, second) == "converging":
                convergence_votes += 1
    if stalemate_votes >= max(1, len(usable) // 2 + 1):
        return "stalemate"
    pair_count = len(usable) * (len(usable) - 1) // 2
    if convergence_votes > pair_count / 2:
        return "converging"
    return "unknown"
