resource "aws_athena_workgroup" "orders" {
  count         = var.enable_athena ? 1 : 0
  name          = "${local.name}-wg"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 1073741824 # 1 GB guard rail

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results[0].bucket}/results/"
    }
  }
}
