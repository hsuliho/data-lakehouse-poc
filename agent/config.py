"""One place for the model name and the fixed context both experiment arms share."""
import os
import socket
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# This network black-holes IPv6: a connection to the API host hangs (curl -6: http=000 after the timeout, curl -4: 0.1 s),
# and Python clients try the IPv6 addresses first, so a single model call can stall for minutes and ignore its own timeout.
# Resolve IPv4 only, for this process. Set AGENT_ALLOW_IPV6=1 on a network where IPv6 works.
if not os.environ.get("AGENT_ALLOW_IPV6"):
    _getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda host, port, family=0, type=0, proto=0, flags=0: _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
load_dotenv(ROOT / ".env")                    # GOOGLE_API_KEY; the key is never read from any other project
if not os.environ.get("GOOGLE_API_KEY"):
    raise RuntimeError("GOOGLE_API_KEY is not set: put it in the environment or in a git-ignored .env at the repository root")

# Models in priority order (override with AGENT_MODELS="a,b"). A model that has used up its daily quota, or does not exist, is
# skipped for the rest of the day and the next one answers. The second id is the owner's "Gemini 3.1 Flash Lite" written in the
# same pattern as the first; it has not been checked against the API. Each result records which model answered, because an A/B
# comparison is only clean when both arms ran on the same model.
MODELS = [m.strip() for m in os.environ.get("AGENT_MODELS", "gemini-3.5-flash-lite,gemini-3.1-flash-lite").split(",") if m.strip()]
MODEL_NAME = MODELS[0]
TODAY = "2026-02-01"                          # fixed so "last week" has one answer (the data covers 2026-01-11 .. 2026-01-30)
TODAY_NOTE = "今天是 2026-02-01(星期日),使用者在台灣(UTC+8)。週一到週日為一週。"
MAX_STEPS = 24                                # graph recursion limit (about 10 tool rounds): a runaway loop ends instead of spending forever
MF = ROOT / ".venv" / "bin" / "mf"
SEMANTIC_DIR = ROOT / "dbt_semantic"
