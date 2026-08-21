"""db-toolbox tools.yaml generation + read-only enforcement."""
from __future__ import annotations

import pytest
import yaml

from lazyclaw.mcp.db_toolbox import (
    DbSource,
    DbTool,
    UnsafeStatement,
    configure_db_toolbox,
    default_tools_yaml,
    ensure_tools_file,
    generate_tools_yaml,
    is_read_only_sql,
    resolve_tools_file,
)


@pytest.mark.parametrize("stmt", [
    "SELECT * FROM stock",
    "select id, qty from stock where qty < 5",
    "WITH low AS (SELECT * FROM stock) SELECT * FROM low",
    "  SELECT 1;  ",
    "EXPLAIN SELECT * FROM stock",
    "SELECT name FROM items -- a comment\n WHERE qty < 3",
])
def test_read_only_accepts(stmt):
    assert is_read_only_sql(stmt) is True


@pytest.mark.parametrize("stmt", [
    "INSERT INTO stock VALUES (1)",
    "UPDATE stock SET qty = 0",
    "DELETE FROM stock",
    "DROP TABLE stock",
    "SELECT * FROM stock; DELETE FROM stock",     # multi-statement
    "SELECT * INTO backup FROM stock",            # SELECT INTO is a write
    "TRUNCATE stock",
    "",
    "   ",
    "GRANT ALL ON stock TO bob",
    "/* hide */ DELETE FROM stock",               # write hidden behind a comment
])
def test_read_only_rejects(stmt):
    assert is_read_only_sql(stmt) is False


def test_generate_yaml_round_trips():
    sources = [DbSource(name="pg", kind="postgres",
                        config={"host": "db", "database": "shop", "user": "ro", "password": "x"})]
    tools = [DbTool(
        name="low_stock", source="pg",
        statement="SELECT sku, qty FROM stock WHERE qty < $1",
        description="items below threshold",
        parameters=[{"name": "threshold", "type": "integer", "description": "min qty"}],
    )]
    out = generate_tools_yaml(sources, tools)
    doc = yaml.safe_load(out)
    assert doc["sources"]["pg"]["kind"] == "postgres"
    assert doc["sources"]["pg"]["host"] == "db"
    assert doc["tools"]["low_stock"]["kind"] == "postgres-sql"
    assert doc["tools"]["low_stock"]["source"] == "pg"
    assert doc["tools"]["low_stock"]["parameters"][0]["name"] == "threshold"


def test_generate_rejects_write_statement():
    sources = [DbSource(name="pg", kind="postgres")]
    tools = [DbTool(name="wipe", source="pg", statement="DELETE FROM stock")]
    with pytest.raises(UnsafeStatement):
        generate_tools_yaml(sources, tools)


def test_generate_rejects_unknown_source():
    tools = [DbTool(name="x", source="ghost", statement="SELECT 1")]
    with pytest.raises(ValueError):
        generate_tools_yaml([], tools)


def test_write_allowed_when_read_only_disabled():
    # Escape hatch exists but is off by default; verify it's actually opt-in.
    sources = [DbSource(name="pg", kind="postgres")]
    tools = [DbTool(name="wipe", source="pg", statement="DELETE FROM stock")]
    out = generate_tools_yaml(sources, tools, read_only=False)
    assert "DELETE FROM stock" in out


def test_default_yaml_is_valid_and_empty():
    doc = yaml.safe_load(default_tools_yaml())
    assert doc == {"sources": {}, "tools": {}}


def test_ensure_tools_file_creates_default(tmp_path):
    path = ensure_tools_file(tmp_path)
    assert path == resolve_tools_file(tmp_path)
    assert path.is_file()
    assert yaml.safe_load(path.read_text()) == {"sources": {}, "tools": {}}
    # Idempotent + doesn't clobber an existing file.
    path.write_text("sources: {}\ntools: {x: 1}\n")
    ensure_tools_file(tmp_path)
    assert yaml.safe_load(path.read_text())["tools"] == {"x": 1}


def test_configure_writes_real_config(tmp_path):
    path = configure_db_toolbox(
        tmp_path,
        [DbSource(name="pg", kind="postgres", config={"host": "db"})],
        [DbTool(name="low_stock", source="pg", statement="SELECT 1 WHERE 1 < $1")],
    )
    doc = yaml.safe_load(path.read_text())
    assert "low_stock" in doc["tools"]
