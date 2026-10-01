# 深度探索：Orchestration 编排模块 (`orchestration/`)

## 1. 模块概述与设计理念

`orchestration/` 模块是整个湖仓数据流转的“中枢神经系统”。基于 **Dagster** 构建，它负责将底层的 Python 加载脚本、SCD2 演进脚本以及上层的 dbt 转换模型与测试融合成一个有机整体。

系统采用基于 **UTC 日期分区（DailyPartitionsDefinition）** 的声明式资产定义，确保每个业务日期的增量数据处理具备完全的幂等性与隔离性。

---

## 2. 核心源码结构与职责

| 文件路径 | 核心职责与功能描述 | 关键符号 / 类 |
| :--- | :--- | :--- |
| `orchestration/definitions.py` | 主定义文件，集成 `dagster-dbt` 翻译器，动态解析 manifest 并生成 Dagster 资产与资产检查 | `Definitions`, `dagster_assets`, `DailyPartitionsDefinition`, `KNOWN_DIRT` |
| `orchestration/keys.py` | 统一管理资产键（Asset Keys），确保跨模块引用一致性 | `DONE_ASSET` |
| `orchestration/state.py` | 管道状态持久化与管理 | State helpers |
| `orchestration/reap.py` | 资源清理与回收脚本 | Reap logic |

---

## 3. 核心设计亮点：dbt 测试到 Dagster 资产检查的映射

在 `orchestration/definitions.py` 中，系统通过 `dagster-dbt` 巧妙地将 dbt 的数据质量测试转化为 Dagster 的原生 Asset Checks（资产检查）。同时，为了应对 POC 注入污染数据的特殊测试需求，设计了 `KNOWN_DIRT` 白名单机制：

```python
# orchestration/definitions.py (节选)
KNOWN_DIRT = {
    "accepted_range_stg_order_items_unit_price__0": 1,
    "accepted_values_stg_orders_order_status__created__paid__shipped__completed__cancelled": 1,
    "accepted_range_fact_orders_line_amount__0": 1,
    "source_unique_ods_orders_raw_event_id": 1,
}
```

这确保了既能严格监控数据质量，又不会因预期的演练污染导致编排流水线不必要地瘫痪。
