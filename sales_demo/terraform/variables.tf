variable "aws_region" {
  description = "Región única autorizada para el piloto comercial."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z0-9]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region debe tener formato de región AWS."
  }
}

variable "expected_aws_account_id" {
  description = "Cuenta autorizada. Se entrega solo por tfvars privado y nunca se versiona."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "expected_aws_account_id debe tener exactamente 12 dígitos."
  }
}

variable "deployment_role_arn" {
  description = "Rol acotado y preexistente que Terraform debe asumir."
  type        = string

  validation {
    condition = can(regex(
      "^arn:[a-z0-9-]+:iam::${var.expected_aws_account_id}:role/APPROVALS-TerraformDeploymentRole$",
      var.deployment_role_arn
    ))
    error_message = "deployment_role_arn debe ser APPROVALS-TerraformDeploymentRole en la cuenta autorizada."
  }
}

variable "deployment_role_contract_sha256" {
  description = "SHA-256 canónico del contrato IAM aprobado para este perfil."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.deployment_role_contract_sha256))
    error_message = "deployment_role_contract_sha256 debe ser un SHA-256 minúsculo."
  }
}

variable "source_revision" {
  description = "Commit Git completo al que queda ligado el plan."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.source_revision))
    error_message = "source_revision debe ser un SHA Git completo."
  }
}

variable "deployment_profile" {
  description = "Perfil aislado del piloto comercial."
  type        = string
  default     = "sales_demo"

  validation {
    condition     = var.deployment_profile == "sales_demo"
    error_message = "Este root module solo admite deployment_profile=sales_demo."
  }
}

variable "project_name" {
  description = "Prefijo corto de recursos."
  type        = string
  default     = "approvals-sales"

  validation {
    condition     = var.project_name == "approvals-sales"
    error_message = "El candidato comercial exige project_name=approvals-sales."
  }
}

variable "environment" {
  description = "Ambiente efímero del piloto."
  type        = string
  default     = "demo"

  validation {
    condition     = var.environment == "demo"
    error_message = "El candidato comercial solo puede usar environment=demo."
  }
}

variable "application_kms_key_arn" {
  description = "CMK persistente de guardrails usada para cifrar task tokens."
  type        = string

  validation {
    condition = can(regex(
      "^arn:[a-z0-9-]+:kms:${var.aws_region}:${var.expected_aws_account_id}:key/[0-9a-f-]{36}$",
      var.application_kms_key_arn
    ))
    error_message = "application_kms_key_arn debe ser una CMK de la cuenta y región autorizadas."
  }
}

variable "application_role_permissions_boundary_arn" {
  description = "Boundary sales_demo preexistente y propiedad exclusiva de guardrails."
  type        = string

  validation {
    condition = can(regex(
      "^arn:[a-z0-9-]+:iam::${var.expected_aws_account_id}:policy/approvals-sales-demo-application-boundary$",
      var.application_role_permissions_boundary_arn
    ))
    error_message = "application_role_permissions_boundary_arn debe ser la boundary sales_demo exacta de guardrails."
  }
}

variable "application_role_permissions_boundary_sha256" {
  description = "SHA-256 canónico de la boundary sales_demo aprobado por guardrails."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.application_role_permissions_boundary_sha256))
    error_message = "application_role_permissions_boundary_sha256 debe ser un SHA-256 minúsculo."
  }
}

variable "guardrail_budget_name" {
  description = "Budget persistente verificado en preflight."
  type        = string

  validation {
    condition     = length(trimspace(var.guardrail_budget_name)) > 0
    error_message = "guardrail_budget_name es obligatorio."
  }
}

variable "guardrail_budget_limit_usd" {
  description = "Alerta mensual existente; no es un límite técnico."
  type        = number
  default     = 20

  validation {
    condition     = var.guardrail_budget_limit_usd == 20
    error_message = "El piloto exige el Budget aprobado de USD 20."
  }
}

variable "api_lambda_package_path" {
  description = "Ruta privada al ZIP reproducible de la Lambda API."
  type        = string

  validation {
    condition     = can(regex("^[^\n\r]+\\.zip$", var.api_lambda_package_path))
    error_message = "api_lambda_package_path debe terminar en .zip."
  }
}

variable "api_lambda_source_code_sha256" {
  description = "SHA-256 base64 del ZIP API, calculado fuera de Terraform."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+/]{43}=$", var.api_lambda_source_code_sha256))
    error_message = "api_lambda_source_code_sha256 debe ser un SHA-256 base64."
  }
}

variable "worker_lambda_package_path" {
  description = "Ruta privada al ZIP reproducible de la Lambda worker/callback."
  type        = string

  validation {
    condition     = can(regex("^[^\n\r]+\\.zip$", var.worker_lambda_package_path))
    error_message = "worker_lambda_package_path debe terminar en .zip."
  }
}

variable "worker_lambda_source_code_sha256" {
  description = "SHA-256 base64 del ZIP worker, calculado fuera de Terraform."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+/]{43}=$", var.worker_lambda_source_code_sha256))
    error_message = "worker_lambda_source_code_sha256 debe ser un SHA-256 base64."
  }
}

variable "frontend_release_sha256" {
  description = "SHA-256 reproducible del árbol dist estático, excluyendo runtime-config.json que se publica post-apply."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.frontend_release_sha256))
    error_message = "frontend_release_sha256 debe ser un SHA-256 minúsculo."
  }
}
