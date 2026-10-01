"""The verification agent's one tool. It gets no others: the evidence (the stored trace, the frozen expectation and the
fixed-rule reading of both) is already in the prompt, so there is nothing for it to go and fetch. One model call.
"""
import json
from typing import Literal

from langchain_core.tools import tool

from agent.verify.evidence import CATEGORIES, facts, classify

STAGE = Literal["tool_choice", "tool_params", "tool_output", "model_misread",
                "format_error", "flow_error", "env_error", "no_problem_found", "unknown"]


@tool
def submit_verdict(stage: STAGE, cause: str, confidence: Literal["high", "medium", "low"], evidence: list[str],
                   ruled_out: list[str], agrees_with_rules: bool) -> str:
    """Submit your FINAL stage verdict for this one failed run. Call this exactly once; do not answer in plain text.
    Args:
      stage: where it went wrong.
        tool_choice     the agent asked for the wrong thing (wrong metric, or a query it wrote itself that is wrong)
        tool_params     the right tool with wrong arguments (date range, day basis, filter)
        tool_output     the tool answered with the wrong data: a platform problem, not the agent's fault
        model_misread   the tools gave the right data and the agent still submitted something else
        format_error    no submit call, or an unusable answer shape
        flow_error      the wrong status, or it stopped / ran on when it should not have
        env_error       the API or a service failed; this run says nothing about quality
        no_problem_found  the run is actually fine
        unknown         the evidence does not settle it; say so rather than guessing
      cause: one or two sentences in Traditional Chinese, naming the step and what went wrong there.
      confidence: high only when a quoted tool call or result shows it directly.
      evidence: each item quotes something from the trace, e.g. 'query_metric(start_date=2026-01-19, end_date=None)'.
                No evidence, no claim.
      ruled_out: stages you considered and rejected, with why, so the verdict can be checked.
      agrees_with_rules: whether you reached the same stage as the fixed rules. Disagreeing is allowed and useful;
                         when you disagree, the cause must say what the rules missed."""
    return "recorded"


def brief(rec: dict, questions: dict | None = None) -> str:
    """Everything the model is given about one failed run: the question, the frozen expectation, what was submitted,
    every tool call with its arguments and the head of its result, and what the fixed rules made of it."""
    f = facts(rec, questions)
    v = classify(f)
    lines = [
        f"題目 {f['id']}({f['arm']} 組):{f['question']}",
        f"預期:{'值 ' + str(f['golden']) if f['golden'] is not None else ''}"
        f"{' 文字 ' + str(f['golden_text']) if f['golden_text'] else ''} 狀態 {f['expected_statuses']}",
        f"實際:狀態 {f['status']},提交的值 {f['submitted_value']}",
        f"評分結果:{f['verdict']}    步數 {f['steps']}    模型 {f['models']}",
        f"依據欄位:{str(f['basis_text'])[:200]}",
        "",
        f"日曆日:題目要求 {f['day_basis']['expected']},查詢使用 {f['day_basis']['used']}",
        f"日期:題目提到 {f['days']['expected']},查詢使用 {f['days']['used']}"
        + (f",未查詢的必要區間 {f['missing_ranges']}" if f["missing_ranges"] else "")
        + (f",未收斂的區間 {f['open_range']}" if f["open_range"] else ""),
        f"預期值是否出現在工具回傳中:{f['golden_in_tools'] or '否'}"
        + ("(這題的答案由模型自行推算,工具不可能回傳)" if f["derived"] else ""),
        "",
        "工具呼叫:",
    ]
    for i, t in enumerate(f["tools"], 1):
        lines.append(f"  [{i}] {t['tool']}({json.dumps(t['args'], ensure_ascii=False)})")
        lines.append(f"      回傳 {t['result_head']}")
    if not f["tools"]:
        lines.append("  (沒有呼叫任何工具)")
    lines += ["", f"固定規則的判定:{v['category']}(信心 {v['confidence']})", f"規則的理由:{v['reason']}"]
    return "\n".join(lines)


@tool
def submit_proposals(proposals: list[dict], no_change_needed: bool = False) -> str:
    """Submit your FINAL prompt proposals for this batch. Call this exactly once; do not answer in plain text.
    Args:
      proposals: one entry per pattern that is worth a prompt change. Each entry has exactly these keys:
        pattern          the shared mistake, with the numbers ('3 of 5 calendar failures left end_date empty')
        cases            the case ids it covers, e.g. ['asof_toys_day/semantic']
        proposal         the exact sentence to add to or change in the system prompt, in Traditional Chinese.
                         Not advice about what to improve: the text itself.
        expected_effect  which cases this should fix
        risk             which OTHER questions this could break, and why. A prompt rule is zero-sum; an empty risk
                         means you have not thought about it. Never leave this blank.
      no_change_needed: true when no pattern justifies a prompt change (too few cases, or the cause is not the prompt).
                        Submitting an empty list with this flag is a valid, useful answer."""
    return "recorded"


VERIFY_TOOLS = []            # the evidence is in the prompt; the only callable is the final tool
SUGGEST_TOOLS = []           # same: the batch summary is in the prompt
