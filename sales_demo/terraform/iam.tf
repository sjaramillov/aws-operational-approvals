locals {
  api_role_name       = "${local.resource_prefix}-api"
  worker_role_name    = "${local.resource_prefix}-worker"
  workflow_role_name  = "${local.resource_prefix}-workflow"
  scheduler_role_name = "${local.resource_prefix}-scheduler"
  schedule_name       = "${local.resource_prefix}-expire"

  api_log_stream_arn    = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.api_function_name}:log-stream:*"
  worker_log_stream_arn = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.worker_function_name}:log-stream:*"
  schedule_arn          = "arn:${local.partition}:scheduler:${var.aws_region}:${local.account_id}:schedule/default/${local.schedule_name}"
  state_execution_arn   = "arn:${local.partition}:states:${var.aws_region}:${local.account_id}:execution:${local.state_machine_name}:*"
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    sid     = "AllowLambdaService"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

  }
}

data "aws_iam_policy_document" "workflow_trust" {
  statement {
    sid     = "AllowStepFunctionsService"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.expected_aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [local.state_machine_arn]
    }
  }
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    sid     = "AllowOnlyExpirySchedule"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.expected_aws_account_id]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [local.schedule_arn]
    }
  }
}

resource "aws_iam_role" "api" {
  name                 = local.api_role_name
  description          = "API runtime for the synthetic sales pilot."
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.application_role_permissions_boundary_arn
  max_session_duration = 3600
}

resource "aws_iam_role" "worker" {
  name                 = local.worker_role_name
  description          = "Worker/callback runtime for the synthetic sales pilot."
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.application_role_permissions_boundary_arn
  max_session_duration = 3600
}

resource "aws_iam_role" "workflow" {
  name                 = local.workflow_role_name
  description          = "Standard workflow runtime for the synthetic sales pilot."
  assume_role_policy   = data.aws_iam_policy_document.workflow_trust.json
  permissions_boundary = var.application_role_permissions_boundary_arn
  max_session_duration = 3600
}

resource "aws_iam_role" "scheduler" {
  name                 = local.scheduler_role_name
  description          = "One-time expiry scheduler for the synthetic sales pilot."
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  permissions_boundary = var.application_role_permissions_boundary_arn
  max_session_duration = 3600
}

data "aws_iam_policy_document" "api" {
  statement {
    sid    = "ReadWriteTenantRecords"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem"
    ]
    resources = [aws_dynamodb_table.sales.arn, "${aws_dynamodb_table.sales.arn}/*"]
  }

  statement {
    sid       = "StartApplicationWorkflow"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [local.state_machine_arn]
  }

  statement {
    sid       = "RecoverOnlySalesExecutions"
    effect    = "Allow"
    actions   = ["states:DescribeExecution", "states:RedriveExecution"]
    resources = [local.state_execution_arn]
  }

  statement {
    sid       = "InvokeOnlyDecisionWorker"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.worker_function_arn]
  }

  statement {
    sid       = "DecryptOnlySalesExecutionMetadata"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.application_kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws:states:stateMachineArn"
      values   = [local.state_machine_arn]
    }
  }

  statement {
    sid       = "WriteApiLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.api_log_stream_arn]
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid    = "MutateOnlyPilotRecords"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem"
    ]
    resources = [aws_dynamodb_table.sales.arn, "${aws_dynamodb_table.sales.arn}/*"]
  }

  statement {
    sid       = "DescribeTokenKey"
    effect    = "Allow"
    actions   = ["kms:DescribeKey"]
    resources = [var.application_kms_key_arn]
  }

  statement {
    sid       = "CryptOnlyApprovalTokens"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt"]
    resources = [var.application_kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:pilot"
      values   = [local.pilot_id]
    }

    condition {
      test     = "Null"
      variable = "kms:EncryptionContext:tenant"
      values   = ["false"]
    }

    condition {
      test     = "Null"
      variable = "kms:EncryptionContext:application"
      values   = ["false"]
    }
  }

  statement {
    sid       = "CompleteOpaqueManagerCallback"
    effect    = "Allow"
    actions   = ["states:SendTaskFailure", "states:SendTaskSuccess"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "DisableAllFourPilotUsersAtExpiry"
    effect    = "Allow"
    actions   = ["cognito-idp:AdminDisableUser"]
    resources = [aws_cognito_user_pool.pilot.arn]
  }

  statement {
    sid       = "WriteWorkerLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.worker_log_stream_arn]
  }
}

data "aws_iam_policy_document" "workflow" {
  statement {
    sid       = "InvokeOnlyWorker"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.worker_function_arn]
  }

  statement {
    sid       = "EncryptOnlySalesWorkflowHistory"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.application_kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:aws:states:stateMachineArn"
      values   = [local.state_machine_arn]
    }
  }

  statement {
    sid    = "DeliverSanitizedWorkflowLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:DescribeLogGroups",
      "logs:DescribeResourcePolicies",
      "logs:GetLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:UpdateLogDelivery"
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokeOnlyExpiryWorker"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.worker_function_arn]
  }
}

resource "aws_iam_policy" "api" {
  name   = "${local.resource_prefix}-api-runtime"
  policy = data.aws_iam_policy_document.api.json

  lifecycle {
    precondition {
      condition     = length(data.aws_iam_policy_document.api.json) < 6144
      error_message = "La policy API excede 6.144 caracteres."
    }
  }
}

resource "aws_iam_policy" "worker" {
  name   = "${local.resource_prefix}-worker-runtime"
  policy = data.aws_iam_policy_document.worker.json

  lifecycle {
    precondition {
      condition     = length(data.aws_iam_policy_document.worker.json) < 6144
      error_message = "La policy worker excede 6.144 caracteres."
    }
  }
}

resource "aws_iam_policy" "workflow" {
  name   = "${local.resource_prefix}-workflow-runtime"
  policy = data.aws_iam_policy_document.workflow.json

  lifecycle {
    precondition {
      condition     = length(data.aws_iam_policy_document.workflow.json) < 6144
      error_message = "La policy workflow excede 6.144 caracteres."
    }
  }
}

resource "aws_iam_policy" "scheduler" {
  name   = "${local.resource_prefix}-scheduler-runtime"
  policy = data.aws_iam_policy_document.scheduler.json

  lifecycle {
    precondition {
      condition     = length(data.aws_iam_policy_document.scheduler.json) < 6144
      error_message = "La policy scheduler excede 6.144 caracteres."
    }
  }
}

resource "aws_iam_role_policy_attachment" "api" {
  role       = aws_iam_role.api.name
  policy_arn = aws_iam_policy.api.arn
}

resource "aws_iam_role_policy_attachment" "worker" {
  role       = aws_iam_role.worker.name
  policy_arn = aws_iam_policy.worker.arn
}

resource "aws_iam_role_policy_attachment" "workflow" {
  role       = aws_iam_role.workflow.name
  policy_arn = aws_iam_policy.workflow.arn
}

resource "aws_iam_role_policy_attachment" "scheduler" {
  role       = aws_iam_role.scheduler.name
  policy_arn = aws_iam_policy.scheduler.arn
}
