"""Arm A tools: what a generic text-to-SQL agent gets. Trino as the read-only `analyst` (5 tables, PII masked).
Experiment control, not a security control: the semantic-layer contract views are hidden from this arm, otherwise
they would hand it the answers (`is_revenue_order`, the Taipei day)."""
import re
from datetime import date, datetime
from decimal import Decimal

import trino
from langchain_core.tools import tool

_conn = None


def _cur():
    global _conn
    if _conn is None:
        _conn = trino.dbapi.connect(host="localhost", port=8080, user="analyst", catalog="iceberg", schema="dwh_spark", timezone="UTC")
    return _conn.cursor()


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


@tool
def list_tables() -> list[str]:
    """List the tables you can query in the data warehouse."""
    c = _cur()
    c.execute("SELECT table_name FROM iceberg.information_schema.tables WHERE table_schema = 'dwh_spark' AND table_type = 'BASE TABLE' ORDER BY 1")
    return [f"dwh_spark.{r[0]}" for r in c.fetchall()]


@tool
def describe_table(table: str) -> dict:
    """Describe a table's columns and types. Args: table: a name from list_tables, e.g. 'dwh_spark.fact_orders'."""
    name = table.split(".")[-1]
    if not re.fullmatch(r"[a-z_]+", name):
        return {"error": "invalid table name"}
    try:
        c = _cur()
        c.execute(f"DESCRIBE iceberg.dwh_spark.{name}")
        return {"table": f"dwh_spark.{name}", "columns": [{"name": r[0], "type": r[1]} for r in c.fetchall()]}
    except Exception as e:
        return {"error": str(getattr(e, "message", e))[:300]}


@tool
def run_sql(sql: str) -> dict:
    """Run one read-only Trino SQL query (SELECT or WITH). Session time zone is UTC. At most 200 rows are returned.
    Args: sql: the query, using fully qualified names such as iceberg.dwh_spark.fact_orders."""
    text = sql.strip().rstrip(";").strip()
    if ";" in text or not re.match(r"(?is)^(select|with)\b", text):
        return {"error": "only a single SELECT or WITH statement is allowed"}
    if re.search(r"(?i)\bsemantic\b", text):
        return {"error": "that schema is not available to you"}
    try:
        c = _cur()
        c.execute(text)
        rows = c.fetchmany(201)
        cols = [d[0] for d in (c.description or [])]
        return {"columns": cols, "rows": [[_plain(v) for v in r] for r in rows[:200]], "truncated": len(rows) > 200}
    except Exception as e:
        return {"error": str(getattr(e, "message", e))[:400]}


RAW_TOOLS = [list_tables, describe_table, run_sql]
