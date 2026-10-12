"""Generate daily retail order CSV files to feed the pipeline.

Each file looks like an export from a point-of-sale system for one day.
A small share of rows are deliberately broken (missing ids, bad numbers,
unparseable dates, duplicate order ids) so the validation and quarantine
path gets exercised.

    python scripts/generate_sample_data.py --days 7 --rows 5000 --out data/raw
"""
import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

STORES = [
    ("S001", "Boston", "Northeast"),
    ("S002", "New York", "Northeast"),
    ("S003", "Philadelphia", "Northeast"),
    ("S004", "Chicago", "Midwest"),
    ("S005", "Detroit", "Midwest"),
    ("S006", "Atlanta", "South"),
    ("S007", "Dallas", "South"),
    ("S008", "Miami", "South"),
    ("S009", "Seattle", "West"),
    ("S010", "San Diego", "West"),
]

PRODUCTS = [
    ("P100", "Wireless Mouse", "electronics", 24.99),
    ("P101", "USB-C Hub", "electronics", 39.50),
    ("P102", "Noise Cancelling Headphones", "electronics", 199.00),
    ("P103", "Mechanical Keyboard", "electronics", 89.95),
    ("P200", "Coffee Beans 1kg", "grocery", 18.75),
    ("P201", "Green Tea 100ct", "grocery", 9.49),
    ("P202", "Olive Oil 1L", "grocery", 14.20),
    ("P300", "Running Shoes", "apparel", 110.00),
    ("P301", "Rain Jacket", "apparel", 75.00),
    ("P302", "Wool Socks 3pk", "apparel", 16.99),
    ("P400", "Desk Lamp", "home", 32.00),
    ("P401", "Cast Iron Pan", "home", 45.00),
]

COLUMNS = [
    "order_id", "order_date", "store_id", "store_city", "store_region",
    "product_id", "product_name", "category", "quantity", "unit_price",
]


def good_row(rng, day, seq):
    store = rng.choice(STORES)
    product = rng.choice(PRODUCTS)
    # prices drift a little day to day
    price = round(product[3] * rng.uniform(0.95, 1.05), 2)
    return {
        "order_id": f"{day:%Y%m%d}-{seq:06d}",
        "order_date": day.isoformat(),
        "store_id": store[0],
        "store_city": store[1],
        "store_region": store[2],
        "product_id": product[0],
        "product_name": product[1],
        "category": product[2],
        "quantity": str(rng.randint(1, 5)),
        "unit_price": f"{price:.2f}",
    }


def break_row(rng, row):
    kind = rng.choice(["missing_id", "bad_qty", "neg_price", "bad_date", "blank_product"])
    if kind == "missing_id":
        row["order_id"] = ""
    elif kind == "bad_qty":
        row["quantity"] = rng.choice(["abc", "0", "-2", ""])
    elif kind == "neg_price":
        row["unit_price"] = "-" + row["unit_price"]
    elif kind == "bad_date":
        row["order_date"] = rng.choice(["2024-13-40", "not a date", ""])
    else:
        row["product_id"] = ""
    return row


def write_day(path, day, rows, bad_rate, dup_rate, rng):
    out = []
    for i in range(rows):
        row = good_row(rng, day, i)
        if rng.random() < bad_rate:
            row = break_row(rng, row)
        out.append(row)
        if rng.random() < dup_rate:
            out.append(dict(row))  # same order exported twice

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    return len(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--rows", type=int, default=5000, help="orders per day before duplicates")
    p.add_argument("--start", default="2024-03-01")
    p.add_argument("--bad-rate", type=float, default=0.02)
    p.add_argument("--dup-rate", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--out", default="data/raw")
    args = p.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)

    for d in range(args.days):
        day = start + timedelta(days=d)
        path = out_dir / f"orders_{day:%Y-%m-%d}.csv"
        n = write_day(path, day, args.rows, args.bad_rate, args.dup_rate, rng)
        print(f"wrote {path} ({n} rows)")


if __name__ == "__main__":
    main()
