data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-etl-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# least privilege: read only from raw/, write only to curated/ and quarantine/
data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/raw/*"]
  }

  statement {
    sid     = "WriteOutputs"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/curated/*",
      "${aws_s3_bucket.data.arn}/quarantine/*",
    ]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.etl.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "etl-access"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}
