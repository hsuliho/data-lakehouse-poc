from typing import Literal

from langchain_core.tools import tool


@tool
def submit_answer(status: Literal["answered", "needs_clarification", "refused", "cannot_answer"], answer: str, basis: str,
                  value: str | None = None, question_to_user: str | None = None) -> str:
    """Submit your FINAL answer to the user. Call this exactly once, when you are done; do not answer in plain text.
    Args:
      status: answered | needs_clarification (you must ask the user something first) | refused (personal data) | cannot_answer.
      answer: one or two sentences for the user, in Traditional Chinese.
      basis: the definitions and the calendar you used (for example which day basis, which orders counted).
      value: the main result. A number as text such as '778.75' for amounts, counts and percentages; a plain name for
             things like a category; omit it when there is no single value.
      question_to_user: the question to ask, only when status is needs_clarification."""
    return "recorded"
