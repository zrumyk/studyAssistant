import subprocess
import sys
import json
from study.runtime import SQL_RUNNER


def test_sql_runner_executes_queries_and_strings_with_semicolons(isolated):
    (isolated / "main.sql").write_text(
        "CREATE TABLE values_table(x INTEGER, label TEXT); INSERT INTO values_table VALUES (2,'a;b'),(4,'c'); SELECT avg(x) AS mean FROM values_table;",
        "utf-8",
    )
    r = subprocess.run(
        [sys.executable, "-c", SQL_RUNNER], cwd=isolated, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    result = json.loads((isolated / "results.json").read_text("utf-8"))
    assert result[-1]["rows"] == [[3.0]]
