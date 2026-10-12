"""Run SQL against the pipeline's Athena workgroup.

Reads the database and workgroup names from `terraform output`, so run it
after `terraform apply`.

    python scripts/run_athena_sql.py --file sql/views.sql
    python scripts/run_athena_sql.py --query "SELECT * FROM v_daily_revenue ORDER BY full_date"
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def tf_outputs():
    out = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-json"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {k: v["value"] for k, v in json.loads(out).items()}


def split_statements(sql):
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def run(athena, sql, database, workgroup):
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
    )["QueryExecutionId"]

    while True:
        q = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = q["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)

    if state != "SUCCEEDED":
        raise RuntimeError(f"{state}: {q['Status'].get('StateChangeReason')}\n{sql}")
    return qid, q["Statistics"]


def print_results(athena, qid, limit=50):
    rows = athena.get_query_results(QueryExecutionId=qid, MaxResults=limit + 1)["ResultSet"]["Rows"]
    table = [[c.get("VarCharValue", "") for c in r["Data"]] for r in rows]
    if not table:
        return
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    for n, r in enumerate(table):
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
        if n == 0:
            print("  ".join("-" * w for w in widths))


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file")
    g.add_argument("--query")
    args = p.parse_args()

    outs = tf_outputs()
    athena = boto3.client("athena")
    statements = split_statements(Path(args.file).read_text()) if args.file else [args.query]

    for sql in statements:
        first_line = sql.splitlines()[0][:80]
        qid, stats = run(athena, sql, outs["glue_database"], outs["athena_workgroup"])
        scanned_kb = stats.get("DataScannedInBytes", 0) / 1024
        print(f"ok  {first_line}  ({stats['EngineExecutionTimeInMillis']} ms, {scanned_kb:.1f} KB scanned)")
        if args.query:
            print_results(athena, qid)


if __name__ == "__main__":
    sys.exit(main())
