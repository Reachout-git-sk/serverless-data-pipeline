import io

import boto3
import pandas as pd
import pytest
from moto import mock_aws

import handler

BUCKET = "test-pipeline-bucket"

HEADER = "order_id,order_date,store_id,store_city,store_region,product_id,product_name,category,quantity,unit_price\n"
GOOD = "20240301-000001,2024-03-01,S001,Boston,Northeast,P100,Wireless Mouse,electronics,2,24.99\n"
GOOD2 = "20240301-000002,2024-03-01,S004,Chicago,Midwest,P200,Coffee Beans 1kg,grocery,1,18.75\n"
BAD = "20240301-000003,2024-03-01,S001,Boston,Northeast,P100,Wireless Mouse,electronics,abc,24.99\n"


def s3_event(key):
    return {"Records": [{"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]}


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        monkeypatch.setattr(handler, "_s3", client)
        yield client


def keys(client, prefix=""):
    resp = client.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return sorted(o["Key"] for o in resp.get("Contents", []))


def read_parquet(client, key):
    body = client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return pd.read_parquet(io.BytesIO(body))


def test_writes_curated_parquet_and_quarantine(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/orders_2024-03-01.csv", Body=HEADER + GOOD + GOOD2 + BAD)

    out = handler.lambda_handler(s3_event("raw/orders_2024-03-01.csv"), None)

    summary = out["processed"][0]
    assert summary["rows_in"] == 3
    assert summary["rows_curated"] == 2
    assert summary["rows_quarantined"] == 1

    assert keys(s3, "curated/") == [
        "curated/dim_date/orders_2024-03-01.parquet",
        "curated/dim_product/orders_2024-03-01.parquet",
        "curated/dim_store/orders_2024-03-01.parquet",
        "curated/fact_orders/orders_2024-03-01.parquet",
    ]
    assert keys(s3, "quarantine/") == ["quarantine/rows/orders_2024-03-01_rejected.csv"]

    fact = read_parquet(s3, "curated/fact_orders/orders_2024-03-01.parquet")
    assert list(fact["order_id"]) == ["20240301-000001", "20240301-000002"]
    assert (fact["source_file"] == f"s3://{BUCKET}/raw/orders_2024-03-01.csv").all()


def test_reupload_overwrites_instead_of_duplicating(s3):
    key = "raw/orders_2024-03-01.csv"
    s3.put_object(Bucket=BUCKET, Key=key, Body=HEADER + GOOD)
    handler.lambda_handler(s3_event(key), None)
    s3.put_object(Bucket=BUCKET, Key=key, Body=HEADER + GOOD + GOOD2)
    handler.lambda_handler(s3_event(key), None)

    assert len(keys(s3, "curated/fact_orders/")) == 1
    fact = read_parquet(s3, "curated/fact_orders/orders_2024-03-01.parquet")
    assert len(fact) == 2


def test_file_with_wrong_schema_is_quarantined_whole(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/broken.csv", Body="id,amount\n1,5\n")

    out = handler.lambda_handler(s3_event("raw/broken.csv"), None)

    assert out["processed"][0]["status"] == "quarantined"
    assert keys(s3, "curated/") == []
    assert keys(s3, "quarantine/") == ["quarantine/files/broken.csv"]


def test_empty_file_is_quarantined(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/empty.csv", Body="")
    out = handler.lambda_handler(s3_event("raw/empty.csv"), None)
    assert out["processed"][0]["status"] == "quarantined"


def test_url_encoded_key_is_decoded(s3):
    s3.put_object(Bucket=BUCKET, Key="raw/orders march.csv", Body=HEADER + GOOD)
    out = handler.lambda_handler(s3_event("raw/orders+march.csv"), None)
    assert out["processed"][0]["status"] == "ok"
    assert "curated/fact_orders/orders march.parquet" in keys(s3, "curated/")
