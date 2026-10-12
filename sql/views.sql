-- Shared metric definitions. Dashboards query these views instead of
-- re-deriving revenue logic, so every chart uses the same numbers.
-- Run with: python scripts/run_athena_sql.py --file sql/views.sql

-- Dimensions are written once per source file, so collapse them to the
-- most recently loaded version of each key.
CREATE OR REPLACE VIEW dim_store_current AS
SELECT store_id, city, region
FROM (
  SELECT *, row_number() OVER (PARTITION BY store_id ORDER BY loaded_at DESC) AS rn
  FROM dim_store
)
WHERE rn = 1;

CREATE OR REPLACE VIEW dim_product_current AS
SELECT product_id, product_name, category
FROM (
  SELECT *, row_number() OVER (PARTITION BY product_id ORDER BY loaded_at DESC) AS rn
  FROM dim_product
)
WHERE rn = 1;

CREATE OR REPLACE VIEW dim_date_current AS
SELECT DISTINCT date_key, full_date, year, quarter, month, day, day_of_week, is_weekend
FROM dim_date;

-- 1. Daily revenue
CREATE OR REPLACE VIEW v_daily_revenue AS
SELECT
  d.full_date,
  d.day_of_week,
  count(DISTINCT f.order_id)  AS orders,
  sum(f.quantity)             AS units,
  round(sum(f.revenue), 2)    AS revenue,
  round(sum(f.revenue) / count(DISTINCT f.order_id), 2) AS avg_order_value
FROM fact_orders f
JOIN dim_date_current d ON f.date_key = d.date_key
GROUP BY d.full_date, d.day_of_week;

-- 2. Orders and revenue by region per month
CREATE OR REPLACE VIEW v_revenue_by_region AS
SELECT
  d.year,
  d.month,
  s.region,
  count(DISTINCT f.order_id) AS orders,
  round(sum(f.revenue), 2)   AS revenue
FROM fact_orders f
JOIN dim_store_current s ON f.store_id = s.store_id
JOIN dim_date_current d  ON f.date_key = d.date_key
GROUP BY d.year, d.month, s.region;

-- 3. Category performance with share of total revenue
CREATE OR REPLACE VIEW v_category_performance AS
SELECT
  p.category,
  sum(f.quantity)          AS units,
  round(sum(f.revenue), 2) AS revenue,
  round(100.0 * sum(f.revenue) / sum(sum(f.revenue)) OVER (), 2) AS pct_of_revenue
FROM fact_orders f
JOIN dim_product_current p ON f.product_id = p.product_id
GROUP BY p.category;

-- 4. Weekend vs weekday sales per store
CREATE OR REPLACE VIEW v_store_weekend_mix AS
SELECT
  s.store_id,
  s.city,
  round(sum(CASE WHEN d.is_weekend THEN f.revenue ELSE 0 END), 2)     AS weekend_revenue,
  round(sum(CASE WHEN NOT d.is_weekend THEN f.revenue ELSE 0 END), 2) AS weekday_revenue
FROM fact_orders f
JOIN dim_store_current s ON f.store_id = s.store_id
JOIN dim_date_current d  ON f.date_key = d.date_key
GROUP BY s.store_id, s.city;

-- Lineage: which file and which run produced the curated rows
CREATE OR REPLACE VIEW v_load_audit AS
SELECT
  source_file,
  max(loaded_at)           AS loaded_at,
  count(*)                 AS fact_rows,
  round(sum(revenue), 2)   AS revenue
FROM fact_orders
GROUP BY source_file;
