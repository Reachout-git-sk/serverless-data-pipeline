"""Validate, clean and split one raw orders file into star schema tables.

Pure pandas, no AWS calls, so it can be unit tested locally.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = [
    "order_id", "order_date", "store_id", "store_city", "store_region",
    "product_id", "product_name", "category", "quantity", "unit_price",
]


class SchemaError(Exception):
    """The file is missing required columns and cannot be processed at all."""


@dataclass
class TransformResult:
    fact_orders: pd.DataFrame
    dim_store: pd.DataFrame
    dim_product: pd.DataFrame
    dim_date: pd.DataFrame
    rejected: pd.DataFrame
    stats: dict = field(default_factory=dict)


def check_schema(df):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SchemaError(f"missing required columns: {', '.join(missing)}")


def _clean_strings(df):
    out = df.copy()
    for col in REQUIRED_COLUMNS:
        out[col] = out[col].fillna("").astype(str).str.strip()
    out["category"] = out["category"].str.lower()
    out["store_region"] = out["store_region"].str.title()
    return out


def _reject_reasons(df):
    """Return a Series with the first failing rule for each row, or '' if the row is fine."""
    qty = pd.to_numeric(df["quantity"], errors="coerce")
    price = pd.to_numeric(df["unit_price"], errors="coerce")
    dates = pd.to_datetime(df["order_date"], format="%Y-%m-%d", errors="coerce")

    rules = [
        ("missing_order_id", df["order_id"] == ""),
        ("missing_store_id", df["store_id"] == ""),
        ("missing_product_id", df["product_id"] == ""),
        ("bad_order_date", dates.isna()),
        ("bad_quantity", qty.isna() | (qty <= 0) | (qty % 1 != 0)),
        ("bad_unit_price", price.isna() | (price < 0)),
    ]
    reasons = pd.Series("", index=df.index)
    # walk rules in reverse so the first rule in the list wins
    for name, mask in reversed(rules):
        reasons = reasons.mask(mask, name)
    return reasons


def build_dim_date(dates):
    d = pd.Series(pd.to_datetime(dates.unique())).sort_values()
    return pd.DataFrame({
        "date_key": d.dt.strftime("%Y%m%d").astype("int32"),
        "full_date": d.dt.date,
        "year": d.dt.year.astype("int16"),
        "quarter": d.dt.quarter.astype("int8"),
        "month": d.dt.month.astype("int8"),
        "day": d.dt.day.astype("int8"),
        "day_of_week": d.dt.day_name(),
        "is_weekend": d.dt.dayofweek >= 5,
    }).reset_index(drop=True)


def transform(raw, source_file, loaded_at=None):
    """Turn a raw orders DataFrame (all columns as strings) into curated tables.

    Every curated row carries source_file and loaded_at so any value in Athena
    can be traced back to the exact file and run that produced it.
    """
    check_schema(raw)
    loaded_at = loaded_at or datetime.now(timezone.utc).replace(microsecond=0)
    rows_in = len(raw)

    df = _clean_strings(raw)
    df["reject_reason"] = _reject_reasons(df)

    # duplicates: only judge rows that passed the other checks, keep the first one
    valid = df["reject_reason"] == ""
    dup = valid & df[valid].duplicated(subset=["order_id"], keep="first").reindex(df.index, fill_value=False)
    df.loc[dup, "reject_reason"] = "duplicate_order_id"

    rejected = df[df["reject_reason"] != ""].copy()
    good = df[df["reject_reason"] == ""].drop(columns="reject_reason").copy()

    good["quantity"] = good["quantity"].astype(float).astype("int32")
    good["unit_price"] = good["unit_price"].astype(float).round(2)
    good["order_date"] = pd.to_datetime(good["order_date"], format="%Y-%m-%d")

    lineage = {"source_file": source_file, "loaded_at": pd.Timestamp(loaded_at)}

    fact = pd.DataFrame({
        "order_id": good["order_id"],
        "date_key": good["order_date"].dt.strftime("%Y%m%d").astype("int32"),
        "store_id": good["store_id"],
        "product_id": good["product_id"],
        "quantity": good["quantity"],
        "unit_price": good["unit_price"],
        "revenue": (good["quantity"] * good["unit_price"]).round(2),
    }).assign(**lineage).reset_index(drop=True)

    dim_store = (
        good[["store_id", "store_city", "store_region"]]
        .drop_duplicates("store_id")
        .rename(columns={"store_city": "city", "store_region": "region"})
        .assign(**lineage).reset_index(drop=True)
    )
    dim_product = (
        good[["product_id", "product_name", "category"]]
        .drop_duplicates("product_id")
        .assign(**lineage).reset_index(drop=True)
    )
    dim_date = build_dim_date(good["order_date"]).assign(**lineage)

    rejected = rejected.assign(source_file=source_file, loaded_at=str(lineage["loaded_at"]))

    stats = {
        "rows_in": rows_in,
        "rows_curated": len(fact),
        "rows_quarantined": len(rejected),
        "pct_quarantined": round(100 * len(rejected) / rows_in, 2) if rows_in else 0.0,
        "reject_reasons": rejected["reject_reason"].value_counts().to_dict(),
    }
    return TransformResult(fact, dim_store, dim_product, dim_date, rejected, stats)
