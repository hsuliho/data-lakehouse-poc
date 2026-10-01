# 深度探索：dbt_semantic 语义层模块 (`dbt_semantic/`)

## 1. 模块概述与设计理念

在传统数据架构中，业务指标口径不一致（“同名不同义”）是令数据团队头疼的顽疾。`dbt_semantic/` 模块引入了 dbt 语义层与 **MetricFlow**，将底层物理表结构抽象为统一的业务实体、维度和度量（Metrics）。

---

## 2. 核心定义与能力

- **Entities（实体）**: 定义业务核心对象（如 `customer`, `product`, `order`）。
- **Dimensions（维度）**: 定义切片与分组维度（如时间、地区、品类）。
- **Measures & Metrics（度量与指标）**: 定义聚合规则（如总收入、订单量、平均客单价）。
- 无论下游是 BI 工具（Superset）还是 AI 代理（Agent），都可以通过 MetricFlow API 获取语义一致的精确计算结果，彻底消灭指标歧义。
