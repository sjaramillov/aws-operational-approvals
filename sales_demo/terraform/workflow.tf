resource "aws_cloudwatch_log_group" "workflow" {
  #checkov:skip=CKV_AWS_338:The approved ephemeral pilot lasts eight days; 14-day retention covers the full window and teardown evidence without retaining workflow metadata for a year.
  name              = "/aws/vendedlogs/states/${local.state_machine_name}"
  retention_in_days = 14
  kms_key_id        = var.application_kms_key_arn
}

resource "aws_sfn_state_machine" "application" {
  #checkov:skip=CKV_AWS_284:X-Ray is outside the bounded pilot; durable states, retries and sanitized ERROR logs are sufficient for this workflow evidence.
  #checkov:skip=CKV_AWS_285:ERROR logging is enabled with execution data deliberately excluded so the task token can never enter CloudWatch; ALL-level input/output history would violate that invariant.
  name     = local.state_machine_name
  role_arn = aws_iam_role.workflow.arn
  type     = "STANDARD"
  definition = templatefile("${path.module}/../workflow.asl.json", {
    WorkerFunctionArn = aws_lambda_function.worker.arn
  })

  encryption_configuration {
    kms_key_id                        = var.application_kms_key_arn
    type                              = "CUSTOMER_MANAGED_KMS_KEY"
    kms_data_key_reuse_period_seconds = 900
  }

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  depends_on = [
    aws_iam_role_policy_attachment.workflow,
    aws_cloudwatch_log_group.workflow
  ]
}
