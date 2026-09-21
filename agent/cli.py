"""Interactive agent: `python -m agent.cli --arm semantic`. If the model needs to ask you something, it does (LangGraph interrupt)."""
import argparse
import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from agent.config import MAX_STEPS
from agent.graph import build_agent, submitted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["semantic", "raw"], default="semantic")
    arm = ap.parse_args().arm
    graph, cfg = build_agent(arm, interactive=True), {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": MAX_STEPS}
    print(f"agent ({arm}) - empty line to quit")
    while (q := input("\n你: ").strip()):
        out = graph.invoke({"messages": [HumanMessage(q)]}, cfg)
        while "__interrupt__" in out:                      # the model asked a clarifying question
            out = graph.invoke(Command(resume=input(f"助理反問: {out['__interrupt__'][0].value}\n你: ")), cfg)
        ans = submitted(out["messages"]) or {}
        print("\n助理:", ans.get("answer", "(no answer submitted)"), "\n     依據:", ans.get("basis"))


if __name__ == "__main__":
    main()
