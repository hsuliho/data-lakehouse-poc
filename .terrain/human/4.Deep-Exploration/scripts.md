# 深度探索：Scripts 运维脚本模块 (`scripts/`)

## 1. 模块概述与设计理念

`scripts/` 模块包含一系列用于湖仓日常运维、数据加载、SCD2 演进、等价性验证及系统合规检查的底层 Python 与 Shell 脚本。

---

## 2. 核心脚本清单与职责

| 脚本文件 | 核心职责 |
| :--- | :--- |
| `scripts/load.py` | 负责将 `landing/dt=YYYY-MM-DD/` 下的 CSV 批次数据加载至 Trino/Iceberg 的 ODS 表。 |
| `scripts/scd2.py` | 执行缓慢变化维历史版本演进与重建。 |
| `scripts/check_equivalence.py` | 验证增量加载结果与全量重跑结果的等价性（Equivalence Check）。 |
| `scripts/check_boundaries.py` | 检查系统边界与接口连通性。 |
| `scripts/check_access.py` | 验证 Trino 访问控制与权限规则。 |
| `scripts/check_pii_consistency.py` | 检查 PII（个人隐私信息）脱敏与一致性。 |
