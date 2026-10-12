from datetime import datetime, timezone

import pandas as pd
import pytest

from transform import REQUIRED_COLUMNS, SchemaError, transform

LOADED_AT = datetime(2024, 3, 2, 6, 0, tzinfo=timezone.utc)


def row(**overrides):
    base = {
        "order_id": "20240301-000001",
        "order_date": "2024-03-01",
        "store_id": "S001",
        "store_city": "Boston",
        "store_region": "Northeast",
        "product_id": "P100",
        "product_name": "Wireless Mouse",
        "category": "electronics",
        "quantity": "2",
        "unit_price": "24.99",
    }
    base.update(overrides)
    return base


def frame(*rows):
    return pd.DataFrame(list(rows), dtype=str)


def run(*rows):
    return transform(frame(*rows), "raw/orders_2024-03-01.csv", LOADED_AT)


def test_clean_row_goes_to_fact_with_revenue():
    r = run(row())
    assert len(r.fact_orders) == 1
    assert r.rejected.empty
    fact = r.fact_orders.iloc[0]
    assert fact["date_key"] == 20240301
    assert fact["quantity"] == 2
    assert fact["revenue"] == pytest.approx(49.98)


def test_lineage_columns_on_every_curated_table():
    r = run(row())
    for table in (r.fact_orders, r.dim_store, r.dim_product, r.dim_date):
        assert (table["source_file"] == "raw/orders_2024-03-01.csv").all()
        assert (table["loaded_at"] == pd.Timestamp(LOADED_AT)).all()


def test_missing_column_raises_schema_error():
    df = frame(row()).drop(columns=["unit_price"])
    with pytest.raises(SchemaError, match="unit_price"):
        transform(df, "raw/x.csv", LOADED_AT)


def test_extra_columns_are_ignored():
    df = frame(row()).assign(coupon_code="SPRING")
    r = transform(df, "raw/x.csv", LOADED_AT)
    assert len(r.fact_orders) == 1
    assert "coupon_code" not in r.fact_orders.columns


@pytest.mark.parametrize("overrides, reason", [
    ({"order_id": ""}, "missing_order_id"),
    ({"store_id": "  "}, "missing_store_id"),
    ({"product_id": ""}, "missing_product_id"),
    ({"order_date": "2024-13-40"}, "bad_order_date"),
    ({"order_date": "03/01/2024"}, "bad_order_date"),
    ({"quantity": "abc"}, "bad_quantity"),
    ({"quantity": "0"}, "bad_quantity"),
    ({"quantity": "-2"}, "bad_quantity"),
    ({"quantity": "1.5"}, "bad_quantity"),
    ({"unit_price": "-3.00"}, "bad_unit_price"),
    ({"unit_price": ""}, "bad_unit_price"),
])
def test_bad_rows_are_quarantined_with_reason(overrides, reason):
    bad = row(order_id="20240301-000002")
    bad.update(overrides)
    r = run(row(), bad)
    assert len(r.rejected) == 1
    assert r.rejected.iloc[0]["reject_reason"] == reason
    assert len(r.fact_orders) == 1


def test_duplicate_order_id_keeps_first():
    r = run(row(quantity="1"), row(quantity="3"))
    assert len(r.fact_orders) == 1
    assert r.fact_orders.iloc[0]["quantity"] == 1
    assert r.rejected.iloc[0]["reject_reason"] == "duplicate_order_id"


def test_bad_row_does_not_shadow_later_good_row_with_same_id():
    r = run(row(quantity="abc"), row(quantity="4"))
    assert len(r.fact_orders) == 1
    assert r.fact_orders.iloc[0]["quantity"] == 4
    assert r.rejected.iloc[0]["reject_reason"] == "bad_quantity"


def test_strings_are_trimmed_and_normalised():
    r = run(row(store_id=" S001 ", category="Electronics ", store_region="northeast"))
    assert r.fact_orders.iloc[0]["store_id"] == "S001"
    assert r.dim_product.iloc[0]["category"] == "electronics"
    assert r.dim_store.iloc[0]["region"] == "Northeast"


def test_dimensions_are_deduplicated():
    r = run(
        row(order_id="a", store_id="S001", product_id="P100"),
        row(order_id="b", store_id="S001", product_id="P101", product_name="USB-C Hub"),
        row(order_id="c", store_id="S002", store_city="New York", product_id="P100"),
    )
    assert sorted(r.dim_store["store_id"]) == ["S001", "S002"]
    assert sorted(r.dim_product["product_id"]) == ["P100", "P101"]


def test_dim_date_attributes():
    r = run(row(order_id="a", order_date="2024-03-02"), row(order_id="b", order_date="2024-03-04"))
    d = r.dim_date.set_index("date_key")
    assert list(d.index) == [20240302, 20240304]
    assert d.loc[20240302, "day_of_week"] == "Saturday"
    assert bool(d.loc[20240302, "is_weekend"]) is True
    assert bool(d.loc[20240304, "is_weekend"]) is False
    assert d.loc[20240304, "quarter"] == 1


def test_stats_report_counts_and_percent():
    r = run(row(order_id="a"), row(order_id="b"), row(order_id="c"), row(order_id=""))
    assert r.stats["rows_in"] == 4
    assert r.stats["rows_curated"] == 3
    assert r.stats["rows_quarantined"] == 1
    assert r.stats["pct_quarantined"] == 25.0
    assert r.stats["reject_reasons"] == {"missing_order_id": 1}


def test_empty_file_produces_empty_tables():
    r = transform(pd.DataFrame(columns=REQUIRED_COLUMNS, dtype=str), "raw/empty.csv", LOADED_AT)
    assert r.fact_orders.empty and r.rejected.empty
    assert r.stats["pct_quarantined"] == 0.0
