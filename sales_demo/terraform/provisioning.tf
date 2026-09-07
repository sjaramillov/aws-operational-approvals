# Contrato temporal para la fase post-deploy. Guardrails debe materializarlo y
# retirarlo fuera de este root antes de ejecutar provision_users.py; este stack
# nunca se autoamplía ni adjunta permisos a su propio rol de despliegue.
data "aws_iam_policy_document" "user_provisioning_contract" {
  statement {
    sid    = "ManageExactlyPilotIdentities"
    effect = "Allow"
    actions = [
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminEnableUser",
      "cognito-idp:AdminGetUser",
      "cognito-idp:AdminListGroupsForUser",
      "cognito-idp:AdminSetUserPassword",
      "cognito-idp:DescribeUserPool",
      "cognito-idp:GetGroup",
      "cognito-idp:ListUsers"
    ]
    resources = [aws_cognito_user_pool.pilot.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid    = "SeedOnlyHashedIdentityProfiles"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:Scan"
    ]
    resources = [aws_dynamodb_table.sales.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

resource "terraform_data" "user_provisioning_contract" {
  input = {
    policy_sha256       = sha256(data.aws_iam_policy_document.user_provisioning_contract.json)
    expected_user_count = 4
    profile_required    = true
    assumed_role_name   = basename(var.deployment_role_arn)
  }

  lifecycle {
    precondition {
      condition     = length(data.aws_iam_policy_document.user_provisioning_contract.json) < 6144
      error_message = "El contrato temporal de provisionamiento excede 6.144 caracteres."
    }
  }
}

output "post_deploy_provisioning_iam_contract" {
  description = "Policy temporal exacta; guardrails la adjunta y retira fuera de este stack."
  value = {
    policy_json         = data.aws_iam_policy_document.user_provisioning_contract.json
    policy_sha256       = sha256(data.aws_iam_policy_document.user_provisioning_contract.json)
    expected_user_count = 4
    remove_after_use    = true
  }
  sensitive = true
}
