"""Lambda entry point: triggered when a CSV lands under raw/ in the data bucket.

Output keys are derived from the source file name, so uploading the same
file twice overwrites the earlier output instead of double counting it.
"""
import io
import json
import logging
import os
import time
import urllib.parse
from pathlib import PurePosixPath

import boto3
import pandas as pd

from transform import SchemaError, transform

log = logging.getLogger()
log.setLevel(logging.INFO)

CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "curated")
QUARANTINE_PREFIX = os.environ.get("QUARANTINE_PREFIX", "quarantine")

_s3 = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _put_parquet(bucket, key, df):
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow", coerce_timestamps="ms", allow_truncated_timestamps=True)
    s3().put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


def _put_csv(bucket, key, df):
    s3().put_object(Bucket=bucket, Key=key, Body=df.to_csv(index=False).encode("utf-8"))


def process_object(bucket, key):
    started = time.perf_counter()
    stem = PurePosixPath(key).stem
    body = s3().get_object(Bucket=bucket, Key=key)["Body"].read()

    try:
        raw = pd.read_csv(io.BytesIO(body), dtype=str, keep_default_na=False)
        result = transform(raw, source_file=f"s3://{bucket}/{key}")
    except (SchemaError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        # whole file is unusable: park a copy in quarantine and keep going
        s3().put_object(Bucket=bucket, Key=f"{QUARANTINE_PREFIX}/files/{PurePosixPath(key).name}", Body=body)
        log.warning(json.dumps({"event": "file_quarantined", "key": key, "error": str(exc)}))
        return {"key": key, "status": "quarantined", "error": str(exc)}

    tables = {
        "fact_orders": result.fact_orders,
        "dim_store": result.dim_store,
        "dim_product": result.dim_product,
        "dim_date": result.dim_date,
    }
    for name, df in tables.items():
        if not df.empty:
            _put_parquet(bucket, f"{CURATED_PREFIX}/{name}/{stem}.parquet", df)

    if not result.rejected.empty:
        _put_csv(bucket, f"{QUARANTINE_PREFIX}/rows/{stem}_rejected.csv", result.rejected)

    summary = {
        "event": "file_processed",
        "key": key,
        "status": "ok",
        **result.stats,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    log.info(json.dumps(summary))
    return summary


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        results.append(process_object(bucket, key))
    return {"processed": results}
