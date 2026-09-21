"""LangGraph agent: model <-> tools loop that ends when the model calls `submit_answer`, plus a human-in-the-loop
clarification step (interrupt) when the submitted answer is a question back to the user."""
import re
import sys
import time
from contextvars import ContextVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from agent import prompts, usage
from agent.config import MAX_STEPS, MODELS
from agent.diagnose.tools import DIAGNOSE_PLAIN_TOOLS, DIAGNOSE_TOOLS, submit_diagnosis
from agent.tools_common import submit_answer
from agent.tools_raw import RAW_TOOLS
from agent.tools_semantic import SEMANTIC_TOOLS

ARMS = {"semantic": (SEMANTIC_TOOLS, prompts.SEMANTIC), "raw": (RAW_TOOLS, prompts.RAW),
        "diagnose": (DIAGNOSE_TOOLS, prompts.DIAGNOSE), "diagnose_plain": (DIAGNOSE_PLAIN_TOOLS, prompts.DIAGNOSE_PLAIN)}
FINAL_TOOL = {"diagnose": submit_diagnosis, "diagnose_plain": submit_diagnosis}
FINALS = ("submit_answer", "submit_diagnosis")                 # the tool that ends a run; which one an arm gets is FINAL_TOOL

# rate-limit bookkeeping for the current run: a 429 is retried after the delay the server suggests, and the wait is reported
_waits: ContextVar[list] = ContextVar("waits", default=[])
_throttled: ContextVar[list] = ContextVar("throttled", default=[])
_models_used: ContextVar[list] = ContextVar("models_used", default=[])


# The key is on the Gemini FREE tier (quota GenerateRequestsPerMinutePerProjectPerModel-FreeTier). Rather than run into 429s,
# every model call in the process waits for a minimum gap (4.5 s = 13 per minute); each 429 doubles the gap (up to 15 s).
_gap = {"seconds": 4.5, "last": 0.0}                       # 15 requests per minute is the free-tier limit


def _throttle() -> float:
    wait = max(0.0, _gap["last"] + _gap["seconds"] - time.time())
    if wait:
        time.sleep(wait)
    _gap["last"] = time.time()
    return wait


def retry_delay(text: str, attempt: int) -> float:
    """Seconds to wait after a 429: the server's own suggestion when the message has one, else exponential."""
    m = re.search(r"retry(?:Delay)?['\": in]+\s*([0-9.]+)\s*s", text, re.I)
    return float(m.group(1)) if m else min(60.0, 10.0 * 2 ** attempt)


def unusable_reason(text: str) -> str | None:
    """Why a model cannot answer for the rest of the day (so the next model should), or None for anything worth retrying.
    A per-minute 429 is NOT this: it passes with a wait."""
    low = text.lower()
    if ("429" in text or "RESOURCE_EXHAUSTED" in text) and ("perday" in low or "per day" in low or "daily" in low):
        return "daily quota used up"
    if "404" in text or "NOT_FOUND" in text or "is not found" in low or "not supported for generatecontent" in low:
        return "model not available"
    return None


def invoke_with_backoff(llm, messages, attempts: int = 6, model: str = ""):
    for attempt in range(attempts):
        try:
            _throttled.get().append(_throttle())
            usage.record_call(model)
            return llm.invoke(messages)
        except Exception as e:
            text = str(e)
            if unusable_reason(text) or ("429" not in text and "RESOURCE_EXHAUSTED" not in text) or attempt == attempts - 1:
                raise
            delay = retry_delay(text, attempt)
            _gap["seconds"] = min(15.0, _gap["seconds"] * 2)
            _waits.get().append({"seconds": round(delay + 1, 1), "quota": re.findall(r"quota[A-Za-z]*['\": ]+([A-Za-z0-9_./-]+)", text)[:2]})
            time.sleep(delay + 1)


def extract_text(content) -> str:
    """Newer Gemini models return a list of content blocks instead of a string."""
    if isinstance(content, str):
        return content
    return "".join(b if isinstance(b, str) else b.get("text", "") for b in content if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))


def submitted(messages) -> dict | None:
    """The arguments of the last submit_answer / submit_diagnosis call, if the model made one."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            for c in reversed(m.tool_calls):
                if c["name"] in FINALS:
                    return c["args"]
    return None


class ModelPool:
    """The models in priority order. `llm` (tests) is one fake used for every name, or {name: fake}."""
    def __init__(self, tools, llm=None):
        self.tools, self.llm, self.cache = tools, llm, {}

    def get(self, name):
        if name not in self.cache:
            base = self.llm[name] if isinstance(self.llm, dict) else self.llm or ChatGoogleGenerativeAI(model=name, temperature=0, max_retries=2, timeout=90)
            self.cache[name] = base.bind_tools(self.tools)
        return self.cache[name]

    def invoke(self, messages):
        tried = []
        for name in [m for m in MODELS if usage.available(m)]:
            try:
                out = invoke_with_backoff(self.get(name), messages, model=name)
                _models_used.get().append(name)
                return out
            except Exception as e:
                why = unusable_reason(str(e))
                if not why:
                    raise
                usage.mark_unavailable(name, why)
                tried.append(f"{name}: {why}")
                print(f"model fallback: {name} unusable ({why})", file=sys.stderr)
        raise RuntimeError("no model can answer today. " + "; ".join(tried or [f"{m}: {usage._load()['unavailable'].get(m, 'request limit reached')}" for m in MODELS]))


def build_agent(arm: str, interactive: bool = False, llm=None):
    tools, system = ARMS[arm]
    final = FINAL_TOOL.get(arm, submit_answer)
    nudge_text = f"請呼叫 {final.name} 工具提交你的最終結果。"
    pool = ModelPool(tools + [final], llm)

    def agent(state: MessagesState):
        return {"messages": [pool.invoke([SystemMessage(system)] + state["messages"])]}

    def route(state: MessagesState):
        last = state["messages"][-1]
        if not last.tool_calls:
            return "nudge"
        return "submit" if any(c["name"] == final.name for c in last.tool_calls) else "tools"

    def nudge(state: MessagesState):
        """The model answered in plain text: ask once for a proper submit_answer, then give up."""
        if any(isinstance(m, HumanMessage) and m.content == nudge_text for m in state["messages"]):
            return Command(goto=END)
        return Command(goto="agent", update={"messages": [HumanMessage(nudge_text)]})

    def submit(state: MessagesState):
        """submit_answer is recorded by its own (trivial) tool, then either the run ends or a human is asked."""
        ans = submitted(state["messages"])
        if interactive and ans and ans["status"] == "needs_clarification":
            reply = interrupt(ans.get("question_to_user") or ans["answer"])
            return Command(goto="agent", update={"messages": [HumanMessage(reply)]})
        return Command(goto=END)

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(tools))
    g.add_node("submit_tool", ToolNode([final]))          # answers the submit_answer call so the history stays valid
    g.add_node("nudge", nudge)
    g.add_node("submit", submit)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", "submit": "submit_tool", "nudge": "nudge"})
    g.add_edge("tools", "agent")
    g.add_edge("submit_tool", "submit")
    return g.compile(checkpointer=MemorySaver())


def run_question(arm: str, question: str, thread_id: str = "run", llm=None) -> dict:
    """Non-interactive: one question, one answer, plus a trace of every tool call and the token usage."""
    graph = build_agent(arm, interactive=False, llm=llm)
    t0, waits, throttled, models = time.time(), [], [], []
    _waits.set(waits)
    _throttled.set(throttled)
    _models_used.set(models)
    try:
        state = graph.invoke({"messages": [HumanMessage(question)]}, {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_STEPS})
    except Exception as e:                                                       # step limit, API failure ...
        return {"arm": arm, "question": question, "error": f"{type(e).__name__}: {str(e)[:1500]}", "models": sorted(set(models)), "rate_limit_waits": waits, "seconds": round(time.time() - t0, 1)}
    msgs, calls, tin, tout = state["messages"], [], 0, 0
    for m in msgs:
        if isinstance(m, AIMessage):
            calls += [{"tool": c["name"], "args": c["args"]} for c in m.tool_calls if c["name"] not in FINALS]
            u = m.usage_metadata or {}
            tin, tout = tin + u.get("input_tokens", 0), tout + u.get("output_tokens", 0)
        elif m.type == "tool" and calls and m.name not in FINALS:
            calls[-1].setdefault("results", []).append(str(m.content)[:400])
    ans = submitted(msgs)
    final_text = extract_text(msgs[-1].content) if isinstance(msgs[-1], AIMessage) else ""
    return {"arm": arm, "question": question, "answer": ans, "raw_answer": (str(ans) if ans else final_text), "tool_calls": calls,
            "steps": len(calls), "llm_calls": len(throttled), "models": sorted(set(models)), "input_tokens": tin, "output_tokens": tout, "rate_limit_waits": waits,
            "throttle_seconds": round(sum(throttled), 1), "seconds": round(time.time() - t0, 1)}
