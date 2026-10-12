variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "orders-pipeline"
}

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas managed layer (provides pandas + pyarrow). See https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html"
  type        = string
  default     = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:31"
}

variable "lambda_memory_mb" {
  type    = number
  default = 512
}

variable "lambda_timeout_s" {
  type    = number
  default = 60
}

variable "alarm_email" {
  description = "Optional address subscribed to the Lambda error alarm. Leave empty to skip."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 14
}
