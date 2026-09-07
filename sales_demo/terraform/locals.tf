locals {
  resource_prefix = "${var.project_name}-${var.environment}"
  partition       = data.aws_partition.current.partition
  account_id      = var.expected_aws_account_id
  pilot_id        = "${var.environment}-sales-plus"

  table_name           = "${local.resource_prefix}-records"
  api_function_name    = "${local.resource_prefix}-api"
  worker_function_name = "${local.resource_prefix}-worker"
  state_machine_name   = "${local.resource_prefix}-application"
  user_pool_name       = "${local.resource_prefix}-users"

  worker_function_arn = "arn:${local.partition}:lambda:${var.aws_region}:${local.account_id}:function:${local.worker_function_name}"
  state_machine_arn   = "arn:${local.partition}:states:${var.aws_region}:${local.account_id}:stateMachine:${local.state_machine_name}"

  kms_encryption_context_keys = ["pilot", "tenant", "application"]

  # Son identidades sintéticas y deliberadamente no incluyen correo ni clave.
  # Se materializan solo post-deploy mediante un procedimiento privado.
  pilot_user_slots = {
    customer_a = { username = "customer-a", tenant_key = "tenant-a", role = "CUSTOMER" }
    manager_a  = { username = "manager-a", tenant_key = "tenant-a", role = "MANAGER" }
    customer_b = { username = "customer-b", tenant_key = "tenant-b", role = "CUSTOMER" }
    manager_b  = { username = "manager-b", tenant_key = "tenant-b", role = "MANAGER" }
  }

  routes = {
    "GET /health"                   = { authorization_type = "NONE", write = false }
    "GET /me"                       = { authorization_type = "JWT", write = false }
    "POST /applications"            = { authorization_type = "JWT", write = true }
    "GET /applications"             = { authorization_type = "JWT", write = false }
    "GET /applications/{id}"        = { authorization_type = "JWT", write = false }
    "GET /approvals"                = { authorization_type = "JWT", write = false }
    "POST /approvals/{id}/decision" = { authorization_type = "JWT", write = true }
    "GET /plan-plus"                = { authorization_type = "JWT", write = false }
  }

  write_route_keys = toset([
    for route_key, route in local.routes : route_key if route.write
  ])

  common_tags = {
    Project            = "APPROVALS Central"
    Component          = "sales-plus-pilot"
    Environment        = var.environment
    ManagedBy          = "Terraform"
    DeploymentProfile  = var.deployment_profile
    DataClassification = "synthetic-only"
    ActivationMode     = "post-readiness"
    SourceRevision     = var.source_revision
  }
}
