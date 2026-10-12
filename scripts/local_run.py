"""Run the pipeline end to end on your machine, no AWS needed.

Processes every CSV in data/raw with the same transform the Lambda uses,
writes the same curated/ and quarantine/ layout under data/out, then loads
the parquet into DuckDB and creates the views from sql/views.sql.

    python scripts/generate_sample_data.py --days 7 --rows 5000
    python scripts/local_run.py
"""
import argparse
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda"))

from transform import SchemaError, transform  # noqa: E402

TABLES = ["fact_orders", "dim_store", "dim_product", "dim_date"]


def process(raw_dir, out_dir):
    totals = {"files": 0, "rows_in": 0, "rows_curated": 0, "rows_quarantined": 0, "seconds": 0.0}
    for path in sorted(raw_dir.glob("*.csv")):
        started = time.perf_counter()
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        try:
            result = transform(raw, source_file=f"raw/{path.name}")
        except SchemaError as exc:
            print(f"{path.name}: quarantined whole file ({exc})")
            continue

        for name in TABLES:
            df = getattr(result, name)
            target = out_dir / "curated" / name / f"{path.stem}.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(target, index=False, coerce_timestamps="ms", allow_truncated_timestamps=True)
        if not result.rejected.empty:
            target = out_dir / "quarantine" / "rows" / f"{path.stem}_rejected.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            result.rejected.to_csv(target, index=False)

        elapsed = time.perf_counter() - started
        s = result.stats
        print(f"{path.name}: {s['rows_in']} in, {s['rows_curated']} curated, "
              f"{s['rows_quarantined']} quarantined ({s['pct_quarantined']}%), {elapsed * 1000:.0f} ms")
        totals["files"] += 1
        totals["seconds"] += elapsed
        for k in ("rows_in", "rows_curated", "rows_quarantined"):
            totals[k] += s[k]
    return totals


def build_views(out_dir):
    con = duckdb.connect()
    for name in TABLES:
        glob = (out_dir / "curated" / name / "*.parquet").as_posix()
        con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{glob}')")
    sql = (ROOT / "sql" / "views.sql").read_text()
    for stmt in [s for s in sql.split(";") if s.strip() and not all(
            ln.strip().startswith("--") or not ln.strip() for ln in s.splitlines())]:
        con.execute(stmt)
    return con


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    p.add_argument("--out", default=str(ROOT / "data" / "out"))
    args = p.parse_args()

    totals = process(Path(args.raw), Path(args.out))
    if not totals["files"]:
        print("no CSV files found; run scripts/generate_sample_data.py first")
        return 1

    pct = 100 * totals["rows_quarantined"] / totals["rows_in"]
    print(f"\n{totals['files']} files, {totals['rows_in']} rows in, {totals['rows_curated']} curated, "
          f"{totals['rows_quarantined']} quarantined ({pct:.2f}%), "
          f"{totals['rows_in'] / totals['seconds']:,.0f} rows/s")

    con = build_views(Path(args.out))
    for view in ["v_daily_revenue", "v_revenue_by_region", "v_category_performance", "v_load_audit"]:
        print(f"\n{view}")
        print(con.execute(f"SELECT * FROM {view} ORDER BY 1, 2").df().to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
