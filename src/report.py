from __future__ import annotations

import json
import re
from html import escape


def _plain_text(value: object, limit: int | None = None) -> str:
    text = str(value or "")
    if limit is not None:
        text = text[:limit]
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _markdown_text(value: object, limit: int | None = None) -> str:
    # Keep agent/user content readable while preventing it from becoming
    # headings, links, or raw HTML when the report is rendered.
    text = _plain_text(value, limit)
    text = escape(text, quote=False)
    return text.replace("\\", "\\\\").replace("`", "\\`").replace("*", "\\*")

def generate_report(
    topic: str,
    participants: list[str],
    end_reason: str | None,
    turns: list[dict],
) -> tuple[str, str]:
    resolved_reason = end_reason or "aborted"
    conclusion = _extract_conclusion(turns, resolved_reason)
    disagreements = [
        _plain_text(turn.get("status_summary"), 240)
        for turn in turns
        if turn.get("status") == "diverging" and turn.get("status_summary")
    ]
    open_questions = [
        _plain_text(turn.get("status_summary"), 240)
        for turn in turns
        if turn.get("status") == "stalemate" and turn.get("status_summary")
    ]
    errors = [
        _plain_text((turn.get("error") or {}).get("message"), 200)
        for turn in turns
        if isinstance(turn.get("error"), dict) and (turn.get("error") or {}).get("message")
    ]
    recommendations = (
        ["Turn the agreed points into concrete next steps and assign an owner."]
        if resolved_reason == "converged"
        else ["Resolve the remaining questions before treating this discussion as an execution plan."]
    )
    risks = ["Validate assumptions manually before executing the generated plan."]
    if errors:
        risks.append(f"Participant errors occurred: {', '.join(errors[:3])}.")
    if resolved_reason in {"degraded", "aborted", "user_ended"}:
        risks.append("The session ended before full roundtable consensus was established.")
    confidence = "high" if resolved_reason == "converged" and not disagreements else "medium" if resolved_reason == "max_rounds" else "low"
    plan_ready = resolved_reason == "converged" and not open_questions and not disagreements and not errors
    plan_ready_reason = "discussion converged" if plan_ready else "discussion is not sufficiently actionable yet"

    participant_values = [str(participant) for participant in participants]
    result = {
        "topic": topic, "participants": participant_values, "end_reason": resolved_reason,
        "final_conclusion": conclusion, "recommendations": recommendations,
        "risks": risks, "open_questions": open_questions, "disagreements": disagreements,
        "confidence": confidence, "plan_ready": plan_ready,
        "plan_ready_reason": plan_ready_reason,
    }

    md = _render_markdown(result, turns, resolved_reason)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    return md, js

def _extract_conclusion(turns: list[dict], end_reason: str) -> str:
    contents = [str(turn.get("content") or "") for turn in turns if str(turn.get("content") or "").strip()]
    if not contents:
        return "The discussion did not produce enough content to form a conclusion."
    last_content = contents[-1]
    if end_reason == "degraded":
        return f"Staged assessment (not roundtable consensus): {_plain_text(last_content, 200)}"
    return _plain_text(last_content, 300)

def _render_markdown(result: dict, turns: list[dict], end_reason: str) -> str:
    lines = ["# Roundtable Report", ""]
    lines.append(f"**Topic:** {_markdown_text(result['topic'])}")
    lines.append(f"**Participants:** {_markdown_text(', '.join(str(value) for value in result['participants']))}")
    lines.append(f"**End Reason:** {_markdown_text(result['end_reason'])}")
    lines.append(f"**Confidence:** {_markdown_text(result['confidence'])}")
    lines.append(f"**Plan Ready:** {result['plan_ready']}")
    lines.append(f"**Plan Ready Reason:** {result['plan_ready_reason']}")
    lines.append("")
    lines.append("## Final Conclusion")
    lines.append(_markdown_text(result["final_conclusion"]))
    lines.append("")
    lines.append("## Recommendations")
    for r in result["recommendations"]:
        lines.append(f"- {_markdown_text(r)}")
    lines.append("")
    lines.append("## Risks")
    for r in result["risks"]:
        lines.append(f"- {_markdown_text(r)}")
    if result["disagreements"]:
        lines.append("")
        lines.append("## Remaining Disagreements")
        for d in result["disagreements"]:
            lines.append(f"- {_markdown_text(d)}")
    if result["open_questions"]:
        lines.append("")
        lines.append("## Open Questions")
        for q in result["open_questions"]:
            lines.append(f"- {_markdown_text(q)}")
    if end_reason == "degraded":
        lines.append("")
        lines.append("> Note: this is a staged degraded-mode assessment, not full roundtable consensus.")
    lines.append("")
    lines.append("## Discussion Log")
    for t in turns:
        lines.append(f"**{_markdown_text(t.get('sender', '?'))}:** {_markdown_text(t.get('content', ''), 200)}")
        lines.append("")
    return "\n".join(lines)
