resource "aws_glue_catalog_database" "orders" {
  count = var.enable_athena ? 1 : 0
  name  = replace("${local.name}_db", "-", "_")
}

locals {
  lineage_columns = [
    { name = "source_file", type = "string" },
    { name = "loaded_at", type = "timestamp" },
  ]

  # column types must match what transform.py writes to parquet
  tables = {
    fact_orders = {
      comment = "One row per order line"
      columns = [
        { name = "order_id", type = "string" },
        { name = "date_key", type = "int" },
        { name = "store_id", type = "string" },
        { name = "product_id", type = "string" },
        { name = "quantity", type = "int" },
        { name = "unit_price", type = "double" },
        { name = "revenue", type = "double" },
      ]
    }
    dim_store = {
      comment = "Store / location dimension"
      columns = [
        { name = "store_id", type = "string" },
        { name = "city", type = "string" },
        { name = "region", type = "string" },
      ]
    }
    dim_product = {
      comment = "Product dimension"
      columns = [
        { name = "product_id", type = "string" },
        { name = "product_name", type = "string" },
        { name = "category", type = "string" },
      ]
    }
    dim_date = {
      comment = "Calendar dimension"
      columns = [
        { name = "date_key", type = "int" },
        { name = "full_date", type = "date" },
        { name = "year", type = "smallint" },
        { name = "quarter", type = "tinyint" },
        { name = "month", type = "tinyint" },
        { name = "day", type = "tinyint" },
        { name = "day_of_week", type = "string" },
        { name = "is_weekend", type = "boolean" },
      ]
    }
  }
}

resource "aws_glue_catalog_table" "curated" {
  for_each      = var.enable_athena ? local.tables : {}
  name          = each.key
  database_name = aws_glue_catalog_database.orders[0].name
  description   = each.value.comment
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification        = "parquet"
    EXTERNAL              = "TRUE"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data.bucket}/curated/${each.key}/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = concat(each.value.columns, local.lineage_columns)
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }
}
