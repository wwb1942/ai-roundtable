from __future__ import annotations
import json

def generate_report(
    topic: str,
    participants: list[str],
    end_reason: str,
    turns: list[dict],
) -> tuple[str, str]:
    conclusion = _extract_conclusion(turns, end_reason)
    recommendations = ["基于讨论内容的建议（需人工审核）"]
    risks = ["讨论中识别的风险点（需人工确认）"]
    open_questions: list[str] = []
    dissent: list[str] = []
    confidence = "high" if end_reason == "converged" else "medium" if end_reason == "max_rounds" else "low"
    plan_ready = end_reason == "converged" and not open_questions

    result = {
        "topic": topic, "participants": participants, "end_reason": end_reason,
        "final_conclusion": conclusion, "recommendations": recommendations,
        "risks": risks, "open_questions": open_questions, "dissent": dissent,
        "confidence": confidence, "plan_ready": plan_ready,
    }

    md = _render_markdown(result, turns, end_reason)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    return md, js

def _extract_conclusion(turns: list[dict], end_reason: str) -> str:
    if not turns:
        return "讨论未产生足够内容以形成结论。"
    last_contents = [t["content"] for t in turns[-2:]]
    if end_reason == "degraded":
        return f"阶段性判断（非圆桌共识）：{last_contents[-1][:200]}"
    return last_contents[-1][:300]

def _render_markdown(result: dict, turns: list[dict], end_reason: str) -> str:
    lines = ["# Roundtable Report", ""]
    lines.append(f"**Topic:** {result['topic']}")
    lines.append(f"**Participants:** {', '.join(result['participants'])}")
    lines.append(f"**End Reason:** {result['end_reason']}")
    lines.append(f"**Confidence:** {result['confidence']}")
    lines.append(f"**Plan Ready:** {result['plan_ready']}")
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
    if result["dissent"]:
        lines.append("")
        lines.append("## Remaining Disagreements")
        for d in result["dissent"]:
            lines.append(f"- {d}")
    if result["open_questions"]:
        lines.append("")
        lines.append("## Open Questions")
        for q in result["open_questions"]:
            lines.append(f"- {q}")
    if end_reason == "degraded":
        lines.append("")
        lines.append("> 注意：本报告为阶段性判断，非完整圆桌共识。")
    lines.append("")
    lines.append("## Discussion Log")
    for t in turns:
        lines.append(f"**{t.get('sender', '?')}:** {t.get('content', '')[:200]}")
        lines.append("")
    return "\n".join(lines)
