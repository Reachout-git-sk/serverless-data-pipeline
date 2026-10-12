"""The Glue tables in terraform/glue.tf and the parquet transform.py writes must agree,
otherwise Athena fails at query time with HIVE_BAD_DATA. Parse glue.tf and compare."""
import io
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from transform import transform
from test_transform import LOADED_AT, frame, row

GLUE_TF = Path(__file__).resolve().parents[1] / "terraform" / "glue.tf"

ARROW_TO_HIVE = {
    pa.string(): "string",
    pa.large_string(): "string",
    pa.int8(): "tinyint",
    pa.int16(): "smallint",
    pa.int32(): "int",
    pa.int64(): "bigint",
    pa.float64(): "double",
    pa.bool_(): "boolean",
    pa.date32(): "date",
}


def glue_columns():
    text = GLUE_TF.read_text()
    lineage = re.findall(r'name = "(\w+)", type = "(\w+)"', text.split("tables = {")[0])
    tables = {}
    for name, body in re.findall(r"\n    (\w+) = \{\n(.*?)\n    \}", text.split("tables = {")[1], re.S):
        tables[name] = dict(re.findall(r'name = "(\w+)", type = "(\w+)"', body) + lineage)
    return tables


def hive_type(arrow_type):
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    return ARROW_TO_HIVE[arrow_type]


def written_schema(df):
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow", coerce_timestamps="ms", allow_truncated_timestamps=True)
    return pq.read_schema(io.BytesIO(buf.getvalue()))


@pytest.mark.parametrize("table", ["fact_orders", "dim_store", "dim_product", "dim_date"])
def test_parquet_types_match_glue_table(table):
    result = transform(frame(row()), "raw/x.csv", LOADED_AT)
    schema = written_schema(getattr(result, table))
    expected = glue_columns()[table]

    actual = {f.name: hive_type(f.type) for f in schema}
    assert actual == expected
