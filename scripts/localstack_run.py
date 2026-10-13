"""Exercise the pipeline deployed on LocalStack (scripts/localstack.sh up) and record what happened.

Uploads every CSV in data/raw to raw/, lets the S3 trigger invoke the Lambda, waits
for the curated parquet, then reads the Lambda's own logs from CloudWatch Logs.
Also re-uploads one file (idempotency), uploads a file with a broken header
(whole-file quarantine), and runs sql/views.sql in DuckDB over the parquet pulled
back from S3, standing in for Athena, which LocalStack Community does not have.

    python scripts/localstack_run.py --out docs/localstack-run.json
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from local_run import build_views  # noqa: E402

ENDPOINT = "http://localhost:4566"
REPORT = re.compile(r"REPORT RequestId: \S+\s+Duration: ([\d.]+) ms.*?(?:Init Duration: ([\d.]+) ms)?", re.S)


def client(name):
    return boto3.client(name, endpoint_url=ENDPOINT, region_name="us-east-1",
                        aws_access_key_id="test", aws_secret_access_key="test")


def tf_outputs():
    out = subprocess.run(["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-json"],
                         check=True, capture_output=True, text=True).stdout
    return {k: v["value"] for k, v in json.loads(out).items()}


def wait_for(s3, bucket, key, timeout=180):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            return s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError:
            time.sleep(0.25)
    raise TimeoutError(f"{key} did not appear within {timeout}s")


def keys(s3, bucket, prefix):
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    return sorted(o["Key"] for p in pages for o in p.get("Contents", []))


def log_events(logs, group):
    events, token = [], None
    for stream in logs.describe_log_streams(logGroupName=group)["logStreams"]:
        kwargs = {"logGroupName": group, "logStreamName": stream["logStreamName"], "startFromHead": True}
        while True:
            page = logs.get_log_events(**kwargs, **({"nextToken": token} if token else {}))
            events += [e["message"] for e in page["events"]]
            if page["nextForwardToken"] == token:
                break
            token = page["nextForwardToken"]
        token = None
    return events


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    p.add_argument("--out", default=str(ROOT / "docs" / "localstack-run.json"))
    args = p.parse_args()

    outs = tf_outputs()
    bucket, group = outs["data_bucket"], outs["log_group"]
    s3, logs = client("s3"), client("logs")
    files = sorted(Path(args.raw).glob("*.csv"))
    if [k for k in keys(s3, bucket, "raw/") if k.endswith(".csv")]:
        print("raw/ already has files, so timings would be wrong; redeploy first "
              "(scripts/localstack.sh down && scripts/localstack.sh up)")
        return 1

    # 1. upload each file and time upload -> fact parquet visible
    end_to_end = {}
    for path in files:
        started = time.perf_counter()
        s3.upload_file(str(path), bucket, f"raw/{path.name}")
        wait_for(s3, bucket, f"curated/fact_orders/{path.stem}.parquet")
        end_to_end[path.name] = round((time.perf_counter() - started) * 1000)
        print(f"{path.name}: curated output after {end_to_end[path.name]} ms")

    # 2. idempotency: the same file again must overwrite, not add
    before = keys(s3, bucket, "curated/")
    first = files[0]
    s3.upload_file(str(first), bucket, f"raw/{first.name}")
    time.sleep(15)
    after = keys(s3, bucket, "curated/")

    # 3. a file with a broken header is parked whole in quarantine/files/
    bad = "orderid,date,store\n1,2024-03-08,S001\n"
    s3.put_object(Bucket=bucket, Key="raw/orders_bad_header.csv", Body=bad.encode())
    wait_for(s3, bucket, "quarantine/files/orders_bad_header.csv")

    # 4. what the Lambda logged
    time.sleep(5)
    messages = log_events(logs, group)
    processed, quarantined, reports = [], [], []
    for m in messages:
        if '"event": "file_processed"' in m:
            processed.append(json.loads(m[m.index("{"):]))
        elif '"event": "file_quarantined"' in m:
            quarantined.append(json.loads(m[m.index("{"):]))
        elif m.startswith("REPORT"):
            d, init = REPORT.search(m).groups()
            reports.append({"duration_ms": float(d), "init_ms": float(init) if init else None})

    first_run = {}
    for rec in processed:  # keep the first record per file (the re-upload is checked separately)
        first_run.setdefault(rec["key"], rec)
    handler_ms = [r["duration_ms"] for r in first_run.values()]
    rows_in = sum(r["rows_in"] for r in first_run.values())
    rows_q = sum(r["rows_quarantined"] for r in first_run.values())

    # 5. query the curated parquet pulled back from S3 with the same views
    with tempfile.TemporaryDirectory() as tmp:
        for key in keys(s3, bucket, "curated/"):
            if key.endswith("/"):  # zone placeholder object
                continue
            target = Path(tmp) / key
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(target))
        con = build_views(Path(tmp))
        fact_rows = con.execute("SELECT count(*) FROM fact_orders").fetchone()[0]
        revenue = con.execute("SELECT round(sum(revenue), 2) FROM fact_orders").fetchone()[0]
        by_region = con.execute("SELECT * FROM v_revenue_by_region ORDER BY 1").df()
        daily = con.execute("SELECT * FROM v_daily_revenue ORDER BY 1").df()

    result = {
        "files": len(files),
        "rows_in": rows_in,
        "rows_quarantined": rows_q,
        "pct_quarantined": round(100 * rows_q / rows_in, 2),
        "handler_ms": {"per_file": {k.split("/")[-1]: v for k, v in zip(first_run, handler_ms)},
                       "median": statistics.median(handler_ms), "max": max(handler_ms)},
        "lambda_report_ms": reports,
        "upload_to_output_ms": end_to_end,
        "idempotent_reupload": {"curated_objects_before": len(before), "after": len(after), "same_keys": before == after},
        "bad_header_quarantined": [q["key"] for q in quarantined],
        "fact_rows_from_s3": fact_rows,
        "total_revenue": revenue,
        "v_revenue_by_region": json.loads(by_region.to_json(orient="records")),
        "v_daily_revenue": json.loads(daily.to_json(orient="records", date_format="iso")),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2))

    durations = [r["duration_ms"] for r in reports]
    inits = [r["init_ms"] for r in reports if r["init_ms"]]
    print(f"\n{len(files)} files, {rows_in} rows in, {rows_q} quarantined ({result['pct_quarantined']}%)")
    print(f"handler time per file: median {result['handler_ms']['median']:.0f} ms, max {result['handler_ms']['max']:.0f} ms")
    if durations:
        print(f"Lambda REPORT duration: median {statistics.median(durations):.0f} ms over {len(durations)} invocations"
              + (f"; cold start init {', '.join(f'{i:.0f}' for i in inits)} ms" if inits else ""))
    print(f"upload -> curated parquet: median {statistics.median(end_to_end.values()):.0f} ms")
    print(f"re-upload: {len(before)} curated objects before, {len(after)} after, same keys: {before == after}")
    print(f"bad header quarantined: {result['bad_header_quarantined']}")
    print(f"fact rows read back from S3: {fact_rows}, revenue {revenue}")
    print(by_region.to_string(index=False))

    problems = []
    if len(first_run) != len(files):
        problems.append(f"Lambda logged {len(first_run)} of {len(files)} files")
    if before != after:
        problems.append("re-upload changed the curated objects")
    if not quarantined:
        problems.append("bad header file was not quarantined")
    if fact_rows != rows_in - rows_q:
        problems.append(f"{fact_rows} fact rows in S3, expected {rows_in - rows_q}")
    for msg in problems:
        print(f"FAIL: {msg}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
