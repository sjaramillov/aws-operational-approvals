output "api_base_url" {
  description = "Endpoint HTTP API; no contiene credenciales."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "web_url" {
  description = "URL CloudFront del PWA privado en origen."
  value       = "https://${aws_cloudfront_distribution.web.domain_name}"
}

output "frontend_runtime_config" {
  description = "Plantilla pública PREPARED para /runtime-config.json; el activador reemplaza expiresAt antes de ACTIVE."
  value = {
    apiBaseUrl        = aws_apigatewayv2_stage.default.invoke_url
    awsRegion         = var.aws_region
    cognitoUserPool   = aws_cognito_user_pool.pilot.id
    cognitoClientId   = aws_cognito_user_pool_client.web.id
    cognitoDomain     = "https://${aws_cognito_user_pool_domain.pilot.domain}.auth.${var.aws_region}.amazoncognito.com"
    redirectUri       = "https://${aws_cloudfront_distribution.web.domain_name}/auth/callback"
    logoutUri         = "https://${aws_cloudfront_distribution.web.domain_name}/"
    oauthFlow         = "authorization_code_pkce"
    scopes            = ["openid"]
    expiresAt         = null
    syntheticDataOnly = true
    deploymentBinding = {
      sourceRevision        = var.source_revision
      frontendReleaseSha256 = var.frontend_release_sha256
    }
  }
}

output "post_deploy_user_provisioning_contract" {
  description = "Cuatro slots sintéticos. El operador crea usuarios, grupos y mapeos sub->tenant sin guardar credenciales en Git/state."
  value = {
    expected_user_count = length(local.pilot_user_slots)
    user_pool_id        = aws_cognito_user_pool.pilot.id
    table_name          = aws_dynamodb_table.sales.name
    slots               = local.pilot_user_slots
    required_mapping = {
      pk         = "IDENTITY#<sha256(cognito_sub)>"
      sk         = "PROFILE"
      attributes = ["tenant_id", "role", "display_name", "enabled"]
    }
    credential_rule = "private_channel_only_never_git_or_terraform_state"
  }
}

output "artifact_upload_contract" {
  description = "Destino de sync post-build; no versiona objetos ni rutas privadas."
  value = {
    bucket_name             = aws_s3_bucket.web.id
    expected_release_sha256 = var.frontend_release_sha256
    release_hash_excludes   = ["runtime-config.json"]
    maximum_uncompressed_mb = 10
    cloudfront_distribution = aws_cloudfront_distribution.web.id
  }
}

output "post_readiness_activation_contract" {
  description = "Entradas no secretas para activate_pilot.py; T0 no existe hasta que el operador ejecuta este contrato después de readiness."
  value = {
    pilot_id                  = local.pilot_id
    table_name                = aws_dynamodb_table.sales.name
    bucket_name               = aws_s3_bucket.web.id
    distribution_id           = aws_cloudfront_distribution.web.id
    api_id                    = aws_apigatewayv2_api.sales.id
    user_pool_id              = aws_cognito_user_pool.pilot.id
    client_id                 = aws_cognito_user_pool_client.web.id
    cognito_domain_prefix     = aws_cognito_user_pool_domain.pilot.domain
    schedule_name             = aws_scheduler_schedule.expiry.name
    worker_arn                = aws_lambda_function.worker.arn
    scheduler_role_arn        = aws_iam_role.scheduler.arn
    source_revision           = var.source_revision
    frontend_release_sha256   = var.frontend_release_sha256
    initial_status            = "PREPARED"
    active_window_hours       = 192
    activation_is_final_write = true
  }
}

output "runtime_contract" {
  description = "Contrato sin secretos compartido por API y worker."
  value = {
    table_name                      = aws_dynamodb_table.sales.name
    state_machine_arn               = aws_sfn_state_machine.application.arn
    max_applications_per_day        = 10
    callback_timeout_seconds        = 3540
    global_workflow_timeout_seconds = 3600
    lambda_memory_mb                = 256
    lambda_timeout_seconds          = 10
    activation_state                = "PREPARED"
    activation_tool                 = "activate_pilot.py"
    active_window_hours             = 192
    task_token_encryption_context = {
      required_keys = local.kms_encryption_context_keys
      pilot         = local.pilot_id
    }
  }
}

output "iam_policy_character_counts" {
  description = "Gate observable: cada managed policy debe permanecer debajo de 6.144 caracteres."
  value = {
    api       = length(data.aws_iam_policy_document.api.json)
    worker    = length(data.aws_iam_policy_document.worker.json)
    workflow  = length(data.aws_iam_policy_document.workflow.json)
    scheduler = length(data.aws_iam_policy_document.scheduler.json)
  }
}

output "deployment_binding" {
  description = "Identidad verificable del candidato sin persistir la cuenta en artefactos públicos."
  value = {
    account_id_hash                 = sha256(var.expected_aws_account_id)
    deployment_profile              = var.deployment_profile
    deployment_role_contract_sha256 = var.deployment_role_contract_sha256
    permissions_boundary_arn        = var.application_role_permissions_boundary_arn
    permissions_boundary_sha256     = var.application_role_permissions_boundary_sha256
    source_revision                 = var.source_revision
    budget_name                     = var.guardrail_budget_name
    budget_limit_usd                = var.guardrail_budget_limit_usd
  }
}
