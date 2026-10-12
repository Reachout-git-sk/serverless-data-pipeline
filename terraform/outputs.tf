output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "lambda_function" {
  value = aws_lambda_function.etl.function_name
}

output "glue_database" {
  value = aws_glue_catalog_database.orders.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.orders.name
}

output "log_group" {
  value = aws_cloudwatch_log_group.etl.name
}
