# 深度探索：dbt_spark 转换建模模块 (`dbt_spark/`)

## 1. 模块概述与设计理念

`dbt_spark/` 模块承载了湖仓中流砥柱的数据清洗与建模工作。基于 **dbt-core** 与 **dbt-spark** 适配器，它在 Apache Spark 计算引擎和 Iceberg 表格式之上实现了严格的 **Medallion Architecture（奖章架构）**。

---

## 2. 核心层次与模型设计

- **Staging 层 (`models/stg/`)**: 对 ODS 原始数据进行字段重命名、基础类型转换与空值清洗。
- **Dimension 层 (`models/scd2/` / `models/dim/`)**: 实现缓慢变化维（SCD Type 2），通过有效起止时间（`valid_from`, `valid_to`）精确追踪客户与产品的历史属性变更。
- **Fact 层 (`models/fact/`)**: 构建核心事实表（如 `fact_orders`），通过主外键关联 Staging 与维度表。
- **DWS / ADS 层 (`models/dws/`, `models/ads/`)**: 汇总每日营业收入（`dws_daily_revenue`）并统计品类收入排名（`ads_category_revenue_rank`）。

---

## 3. 数据质量保障

每个模型均配置了完善的 schema.yml 测试，涵盖 `unique`, `not_null`, `accepted_values`, `relationships` 等，确保流转到下游的数据绝对可信。
