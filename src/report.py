from __future__ import annotations
import json

def generate_report(
    topic: str,
    participants: list[str],
    end_reason: str,
    turns: list[dict],
) -> tuple[str, str]:
    conclusion = _extract_conclusion(turns, end_reason)
    recommendations = ["Review the discussion log and turn the agreed points into concrete next steps."]
    risks = ["Validate assumptions manually before executing the generated plan."]
    open_questions: list[str] = []
    disagreements: list[str] = []
    confidence = "high" if end_reason == "converged" else "medium" if end_reason == "max_rounds" else "low"
    plan_ready = end_reason == "converged" and not open_questions
    plan_ready_reason = "discussion converged" if plan_ready else "discussion is not sufficiently actionable yet"

    result = {
        "topic": topic, "participants": participants, "end_reason": end_reason,
        "final_conclusion": conclusion, "recommendations": recommendations,
        "risks": risks, "open_questions": open_questions, "disagreements": disagreements,
        "confidence": confidence, "plan_ready": plan_ready,
        "plan_ready_reason": plan_ready_reason,
    }

    md = _render_markdown(result, turns, end_reason)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    return md, js

def _extract_conclusion(turns: list[dict], end_reason: str) -> str:
    if not turns:
        return "The discussion did not produce enough content to form a conclusion."
    last_contents = [t["content"] for t in turns[-2:]]
    if end_reason == "degraded":
        return f"Staged assessment (not roundtable consensus): {last_contents[-1][:200]}"
    return last_contents[-1][:300]

def _render_markdown(result: dict, turns: list[dict], end_reason: str) -> str:
    lines = ["# Roundtable Report", ""]
    lines.append(f"**Topic:** {result['topic']}")
    lines.append(f"**Participants:** {', '.join(result['participants'])}")
    lines.append(f"**End Reason:** {result['end_reason']}")
    lines.append(f"**Confidence:** {result['confidence']}")
    lines.append(f"**Plan Ready:** {result['plan_ready']}")
    lines.append(f"**Plan Ready Reason:** {result['plan_ready_reason']}")
    lines.append("")
    lines.append("## Final Conclusion")
    lines.append(result["final_conclusion"])
    lines.append("")
    lines.append("## Recommendations")
    for r in result["recommendations"]:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("## Risks")
    for r in result["risks"]:
        lines.append(f"- {r}")
    if result["disagreements"]:
        lines.append("")
        lines.append("## Remaining Disagreements")
        for d in result["disagreements"]:
            lines.append(f"- {d}")
    if result["open_questions"]:
        lines.append("")
        lines.append("## Open Questions")
        for q in result["open_questions"]:
            lines.append(f"- {q}")
    if end_reason == "degraded":
        lines.append("")
        lines.append("> Note: this is a staged degraded-mode assessment, not full roundtable consensus.")
    lines.append("")
    lines.append("## Discussion Log")
    for t in turns:
        lines.append(f"**{t.get('sender', '?')}:** {t.get('content', '')[:200]}")
        lines.append("")
    return "\n".join(lines)
