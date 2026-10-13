data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = var.lambda_package_dir != "" ? var.lambda_package_dir : "${path.module}/../lambda"
  output_path = "${path.module}/build/lambda.zip"
  excludes    = ["__pycache__"]
}

locals {
  code_via_s3 = var.lambda_package_dir != ""
}

resource "aws_s3_object" "lambda_code" {
  count       = local.code_via_s3 ? 1 : 0
  bucket      = aws_s3_bucket.data.id
  key         = "_artifacts/lambda-${data.archive_file.lambda.output_md5}.zip"
  source      = data.archive_file.lambda.output_path
  source_hash = data.archive_file.lambda.output_md5
}

resource "aws_cloudwatch_log_group" "etl" {
  name              = "/aws/lambda/${local.name}-etl"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "etl" {
  function_name = "${local.name}-etl"
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  # large bundled packages go through S3; the direct upload limit is ~70 MB
  filename         = local.code_via_s3 ? null : data.archive_file.lambda.output_path
  s3_bucket        = local.code_via_s3 ? aws_s3_object.lambda_code[0].bucket : null
  s3_key           = local.code_via_s3 ? aws_s3_object.lambda_code[0].key : null
  source_code_hash = data.archive_file.lambda.output_base64sha256
  layers           = var.pandas_layer_arn != "" ? [var.pandas_layer_arn] : []
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_s

  environment {
    variables = {
      CURATED_PREFIX    = "curated"
      QUARANTINE_PREFIX = "quarantine"
    }
  }

  depends_on = [aws_cloudwatch_log_group.etl]
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id   = "AllowS3Invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.etl.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.data.arn
  source_account = data.aws_caller_identity.current.account_id
}

data "aws_caller_identity" "current" {}
