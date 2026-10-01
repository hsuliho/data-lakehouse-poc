# 深度探索：Agent 智能代理模块 (`agent/`)

## 1. 模块概述与设计理念

在现代化数据湖仓中，运维排障与指标查询往往需要繁琐的 SQL 编写与日志检索。`agent/` 模块正是为了打破这一痛点而生。它基于 **LangGraph** 状态图与 **Google Gemini** 大语言模型，构建了一个具备多角色（Arms）、工具自动调用、指数退避流控以及人类干预（Human-in-the-loop）能力的智能代理系统。

如同现代化工厂中的“高级巡检工”，Agent 能够在无人值守时自主诊断管道故障，也能在分析师提问时精准调用语义层获取指标。

---

## 2. 核心源码结构与职责

| 文件路径 | 核心职责与功能描述 | 关键符号 / 类 |
| :--- | :--- | :--- |
| `agent/graph.py` | 核心 LangGraph 状态图定义，管理 Model ↔ Tool 循环、限流与中断恢复 | `StateGraph`, `invoke_with_backoff`, `submitted` |
| `agent/cli.py` | 命令行交互入口，供用户发起诊断或查询会话 | `main()` |
| `agent/config.py` | 代理配置管理（模型选择、最大步数等） | `MODELS`, `MAX_STEPS` |
| `agent/tools_common.py`| 通用基础工具集（如 `submit_answer` 等结束工具） | `submit_answer` |
| `agent/tools_raw.py` | 原始 SQL 执行工具，直接与 Trino 交互进行底层排查 | `RAW_TOOLS` |
| `agent/tools_semantic.py`| 语义层查询工具，调用 MetricFlow 获取结构化指标 | `SEMANTIC_TOOLS` |
| `agent/diagnose/` | 自动化故障诊断与证据收集子模块 | `submit_diagnosis` |
| `agent/verify/` | 验证与建议提交通道 | `submit_proposals`, `submit_verdict` |

---

## 3. 关键算法与稳健性设计：429 限流与退避

由于 Gemini 免费层对每分钟请求数（RPM）有严格限制，直接并发调用极易触发 `429 Too Many Requests` 或 `RESOURCE_EXHAUSTED` 错误。为此，`agent/graph.py` 设计了精妙的平滑限流与指数退避机制：

```python
# agent/graph.py (节选自 invoke_with_backoff)
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
            time.sleep(delay + 1)
```

这段代码通过维护一个动态的全局时间间隔 (`_gap`)，在每次模型调用前主动进行微调等待，并在遇到 429 时自动解析服务器建议的重试延迟（或进行指数退避），确保代理在受限配额下也能稳定可靠地完成复杂诊断任务。
