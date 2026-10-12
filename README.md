# Serverless Data Pipeline (AWS)

Event-driven ETL on AWS. Daily retail order CSVs land in S3, a Python Lambda
validates and cleans them, good rows are written as Parquet into a star schema,
bad rows are routed to a quarantine folder, and the curated data is queried with
SQL in Athena through shared metric views. All infrastructure is defined in
Terraform, so the whole thing comes up with one command and goes away with another.

```mermaid
flowchart LR
    A[CSV upload] -->|s3:ObjectCreated<br/>prefix raw/, suffix .csv| B[(S3 raw/)]
    B --> C[Lambda<br/>validate + clean]
    C -->|good rows, Parquet| D[(S3 curated/<br/>fact_orders, dim_store,<br/>dim_product, dim_date)]
    C -->|bad rows + reason| E[(S3 quarantine/)]
    D --> F[Glue Data Catalog]
    F --> G[Athena views<br/>daily revenue, region,<br/>category, load audit]
    C -. logs .-> H[CloudWatch Logs]
    C -. Errors metric .-> I[CloudWatch alarm] --> J[SNS email]
```

## What it does

| Step | Detail |
|---|---|
| Trigger | S3 event notification on `raw/*.csv` invokes the Lambda |
| Schema check | File must contain all 10 required columns, otherwise the whole file is copied to `quarantine/files/` |
| Row validation | Missing ids, unparseable dates, non-integer or non-positive quantity, negative or missing price, duplicate `order_id` |
| Cleaning | Trim whitespace, normalise category and region casing, cast types, compute `revenue` |
| Curated output | Parquet per table under `curated/<table>/<source file>.parquet` |
| Quarantine | Rejected rows with a `reject_reason` column under `quarantine/rows/` |
| Lineage | Every curated row carries `source_file` and `loaded_at` |
| Catalog | Four Glue tables over the curated prefixes, queryable from Athena |
| Metrics | SQL views in [`sql/views.sql`](sql/views.sql) so dashboards share one definition of revenue |
| Monitoring | CloudWatch log group (14 day retention) and an alarm on Lambda `Errors >= 1` over 5 minutes, sent to SNS |

### Star schema

```mermaid
erDiagram
    fact_orders }o--|| dim_date : date_key
    fact_orders }o--|| dim_store : store_id
    fact_orders }o--|| dim_product : product_id
    fact_orders {
        string order_id
        int date_key
        string store_id
        string product_id
        int quantity
        double unit_price
        double revenue
        string source_file
        timestamp loaded_at
    }
    dim_date {
        int date_key
        date full_date
        smallint year
        tinyint quarter
        tinyint month
        string day_of_week
        boolean is_weekend
    }
    dim_store {
        string store_id
        string city
        string region
    }
    dim_product {
        string product_id
        string product_name
        string category
    }
```

Dimension rows are written once per source file. The `dim_*_current` views keep
the latest version of each key (by `loaded_at`), and the metric views join on those.

### Re-uploading the same file

Output keys are derived from the source file name, so uploading
`orders_2024-03-01.csv` again overwrites that file's curated Parquet rather than
adding a second copy. Revenue is never double counted for a re-sent file.

## Dataset

[`scripts/generate_sample_data.py`](scripts/generate_sample_data.py) produces one CSV per day, shaped like a
point-of-sale export: 10 stores in 4 regions, 12 products in 4 categories.
About 2% of rows are deliberately broken and about 1% are exported twice, so the
validation and quarantine path always has work to do. The generator is seeded,
so runs are reproducible.

## Project layout

```
lambda/
  handler.py        S3 event -> read CSV -> transform -> write Parquet / quarantine
  transform.py      validation, cleaning, star schema split (pure pandas, no AWS)
terraform/          S3, Lambda, IAM, S3 trigger, Glue, Athena, CloudWatch, SNS
sql/views.sql       shared metric views
scripts/
  generate_sample_data.py
  local_run.py      full pipeline on your machine with DuckDB, no AWS needed
  run_athena_sql.py run views.sql or ad hoc SQL against the deployed workgroup
tests/              pytest: transform rules, Lambda against mocked S3, schema contract
```

## Run the tests

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -v
```

31 tests cover:

- every validation rule and its reject reason
- duplicate handling (first valid copy wins, a bad row does not block a later good one)
- lineage columns on every curated table
- the Lambda handler end to end against mocked S3 (moto): output layout, re-upload idempotency, whole-file quarantine, URL-encoded keys
- a schema contract test that parses `terraform/glue.tf` and checks the Parquet column types match, so a type change cannot silently break Athena

## Run locally (no AWS)

```bash
python scripts/generate_sample_data.py --days 7 --rows 5000
python scripts/local_run.py
```

This runs the same `transform.py` over every file, writes the same `curated/` and
`quarantine/` layout under `data/out/`, then creates the views from
`sql/views.sql` in DuckDB and prints them.

Measured locally (7 files, Windows laptop, Python 3.11):

| Metric | Value |
|---|---|
| Rows in | 35,351 |
| Rows curated | 34,283 |
| Rows quarantined | 1,068 (3.02%) |
| Transform time per file (~5,050 rows) | 97 to 172 ms |
| Throughput | ~44,000 rows/s |

## Deploy to AWS

Requires Terraform 1.6+, the AWS CLI with credentials configured, and Python for the helper scripts.

```bash
cd terraform
terraform init
terraform apply                       # optional: -var alarm_email=you@example.com
cd ..

BUCKET=$(terraform -chdir=terraform output -raw data_bucket)
python scripts/generate_sample_data.py --days 7 --rows 5000
aws s3 cp data/raw/ s3://$BUCKET/raw/ --recursive

python scripts/run_athena_sql.py --file sql/views.sql
python scripts/run_athena_sql.py --query "SELECT * FROM v_daily_revenue ORDER BY full_date"
```

Lambda run summaries (rows in, curated, quarantined, duration) are logged as JSON
to the CloudWatch log group shown by `terraform output log_group`.

The Lambda uses the AWS SDK for pandas managed layer for pandas and pyarrow. The
default ARN is for `us-east-1`. For other regions, set `pandas_layer_arn` from the
[layer list](https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html).

## Tear down

```bash
terraform -chdir=terraform destroy
```

Buckets are created with `force_destroy`, so this removes the data too.

## Design notes

- **Why serverless:** files arrive a few times a day. A Lambda that runs for a few
  hundred milliseconds per file costs almost nothing, compared with an always-on instance.
- **Least-privilege IAM:** the function can only read `raw/*` and only write to `curated/*` and `quarantine/*`.
- **Why the pure-pandas `transform.py`:** all business rules live outside the handler, so they are tested without AWS and reused by the local runner.
- **Athena guard rail:** the workgroup enforces a 1 GB scan limit per query, and query results expire after 7 days.
