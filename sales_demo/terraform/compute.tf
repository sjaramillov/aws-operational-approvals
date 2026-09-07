resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_338:The approved ephemeral pilot lasts eight days; 14-day retention covers the full window and teardown evidence without retaining synthetic operations for a year.
  name              = "/aws/lambda/${local.api_function_name}"
  retention_in_days = 14
  kms_key_id        = var.application_kms_key_arn
}

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_338:The approved ephemeral pilot lasts eight days; 14-day retention covers the full window and teardown evidence without retaining synthetic operations for a year.
  name              = "/aws/lambda/${local.worker_function_name}"
  retention_in_days = 14
  kms_key_id        = var.application_kms_key_arn
}

resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_50:X-Ray is excluded from the bounded pilot; durable DynamoDB/workflow state and allowlisted logs are the approved evidence sources.
  #checkov:skip=CKV_AWS_116:HTTP API invocation is synchronous; application state is transactional and StartExecution retries are idempotent, so a Lambda DLQ would not replay the business transaction safely.
  #checkov:skip=CKV_AWS_117:The Lambda only calls regional AWS public endpoints; adding a VPC/NAT would increase cost and failure modes without isolating a private data source.
  #checkov:skip=CKV_AWS_173:Environment values contain only identifiers and limits; no secret or task token is stored there, and the task token itself uses an explicit CMK context.
  #checkov:skip=CKV_AWS_272:Reproducible ZIP hashes bind the reviewed code; AWS Signer is outside this eight-day synthetic pilot.
  #checkov:skip=CKV_AWS_115:Reserved concurrency is not configured in this candidate. VERIFY quota and concurrency policy in the destination account before deployment.
  function_name = local.api_function_name
  description   = "Tenant-safe API for the synthetic APPROVALS sales pilot."
  role          = aws_iam_role.api.arn
  handler       = "sales_demo.backend.lambda_api.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = var.api_lambda_package_path
  source_code_hash = var.api_lambda_source_code_sha256

  memory_size = 256
  timeout     = 10

  environment {
    variables = {
      SALES_PILOT_ID             = local.pilot_id
      SALES_TABLE_NAME           = aws_dynamodb_table.sales.name
      SALES_STATE_MACHINE_ARN    = local.state_machine_arn
      SALES_WORKER_FUNCTION_NAME = local.worker_function_name
      SALES_DAILY_LIMIT          = "10"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.api,
    aws_iam_role_policy_attachment.api,
    terraform_data.deployment_contract
  ]
}

resource "aws_lambda_function" "worker" {
  #checkov:skip=CKV_AWS_50:X-Ray is excluded from the bounded pilot; durable DynamoDB/workflow state and allowlisted logs are the approved evidence sources.
  #checkov:skip=CKV_AWS_116:Step Functions and Scheduler own bounded retries; a Lambda DLQ would duplicate callback/finalizer effects instead of preserving their conditional-write protocol.
  #checkov:skip=CKV_AWS_117:The Lambda only calls regional AWS public endpoints; adding a VPC/NAT would increase cost and failure modes without isolating a private data source.
  #checkov:skip=CKV_AWS_173:Environment values contain only identifiers and an allowlisted synthetic username set; task tokens are encrypted separately with CMK context.
  #checkov:skip=CKV_AWS_272:Reproducible ZIP hashes bind the reviewed code; AWS Signer is outside this eight-day synthetic pilot.
  #checkov:skip=CKV_AWS_115:Reserved concurrency is not configured in this candidate. VERIFY quota and concurrency policy in the destination account before deployment.
  function_name = local.worker_function_name
  description   = "Approval-token, finalization and expiry worker for the synthetic APPROVALS sales pilot."
  role          = aws_iam_role.worker.arn
  handler       = "sales_demo.backend.lambda_worker.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = var.worker_lambda_package_path
  source_code_hash = var.worker_lambda_source_code_sha256

  memory_size = 256
  timeout     = 10

  environment {
    variables = {
      SALES_PILOT_ID     = local.pilot_id
      SALES_TABLE_NAME   = aws_dynamodb_table.sales.name
      SALES_KMS_KEY_ID   = var.application_kms_key_arn
      SALES_USER_POOL_ID = aws_cognito_user_pool.pilot.id
      SALES_SYNTHETIC_USERNAMES = join(",", [
        local.pilot_user_slots.customer_a.username,
        local.pilot_user_slots.manager_a.username,
        local.pilot_user_slots.customer_b.username,
        local.pilot_user_slots.manager_b.username
      ])
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.worker,
    aws_iam_role_policy_attachment.worker,
    terraform_data.deployment_contract
  ]
}
