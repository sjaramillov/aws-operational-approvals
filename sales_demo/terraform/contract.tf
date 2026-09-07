resource "terraform_data" "deployment_contract" {
  input = {
    account_id_hash                 = sha256(var.expected_aws_account_id)
    aws_region                      = var.aws_region
    deployment_profile              = var.deployment_profile
    deployment_role_contract_sha256 = var.deployment_role_contract_sha256
    permissions_boundary_arn        = var.application_role_permissions_boundary_arn
    permissions_boundary_sha256     = var.application_role_permissions_boundary_sha256
    source_revision                 = var.source_revision
    budget_name                     = var.guardrail_budget_name
    budget_limit_usd                = var.guardrail_budget_limit_usd
    frontend_release_sha256         = var.frontend_release_sha256
    activation_state                = "PREPARED"
    activation_window_hours         = 192
  }

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.expected_aws_account_id
      error_message = "La sesión asumida no pertenece a la cuenta autorizada."
    }

    precondition {
      condition     = var.deployment_role_arn == "arn:${data.aws_partition.current.partition}:iam::${var.expected_aws_account_id}:role/APPROVALS-TerraformDeploymentRole"
      error_message = "Terraform solo puede asumir APPROVALS-TerraformDeploymentRole."
    }

    precondition {
      condition     = var.deployment_profile == "sales_demo"
      error_message = "El root module comercial rechaza cualquier otro perfil."
    }

    precondition {
      condition = alltrue([
        for role in [aws_iam_role.api, aws_iam_role.worker, aws_iam_role.workflow, aws_iam_role.scheduler] :
        role.permissions_boundary == var.application_role_permissions_boundary_arn
      ])
      error_message = "Los cuatro roles runtime deben usar la boundary externa de guardrails."
    }

    precondition {
      condition     = length(local.pilot_user_slots) == 4
      error_message = "El contrato exige exactamente cuatro slots de identidad sintética."
    }

  }
}
