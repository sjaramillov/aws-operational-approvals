resource "aws_cognito_user_pool" "pilot" {
  name                     = local.user_pool_name
  user_pool_tier           = "LITE"
  deletion_protection      = "INACTIVE"
  mfa_configuration        = "OPTIONAL"
  username_attributes      = []
  auto_verified_attributes = []

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 1
  }

  software_token_mfa_configuration {
    enabled = true
  }

  username_configuration {
    case_sensitive = false
  }

  schema {
    attribute_data_type = "String"
    mutable             = false
    name                = "tenant_key"
    required            = false

    string_attribute_constraints {
      min_length = 1
      max_length = 32
    }
  }

  schema {
    attribute_data_type = "String"
    mutable             = false
    name                = "pilot_role"
    required            = false

    string_attribute_constraints {
      min_length = 7
      max_length = 8
    }
  }

}

resource "aws_cognito_user_pool_domain" "pilot" {
  domain                = "${local.resource_prefix}-${substr(sha256(var.expected_aws_account_id), 0, 8)}"
  user_pool_id          = aws_cognito_user_pool.pilot.id
  managed_login_version = 1
}

resource "aws_cognito_user_group" "customer" {
  name         = "customer"
  user_pool_id = aws_cognito_user_pool.pilot.id
  description  = "Customers sintéticos; la autorización efectiva se resuelve por sub en DynamoDB."
  precedence   = 20
}

resource "aws_cognito_user_group" "manager" {
  name         = "manager"
  user_pool_id = aws_cognito_user_pool.pilot.id
  description  = "Managers sintéticos; la autorización efectiva se resuelve por sub en DynamoDB."
  precedence   = 10
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "${local.resource_prefix}-web"
  user_pool_id = aws_cognito_user_pool.pilot.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid"]
  supported_identity_providers         = ["COGNITO"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH"]

  callback_urls = ["https://${aws_cloudfront_distribution.web.domain_name}/auth/callback"]
  logout_urls   = ["https://${aws_cloudfront_distribution.web.domain_name}/"]

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  refresh_token_rotation {
    feature                    = "ENABLED"
    retry_grace_period_seconds = 10
  }

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 8

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  auth_session_validity = 3

  # PKCE se implementa en el cliente público. No existe client secret y las
  # redirect URIs quedan ligadas a esta distribución concreta.
  read_attributes  = ["sub"]
  write_attributes = []
}

# Terraform/API clients do not receive a browser login style implicitly. The
# classic Hosted UI is pinned deliberately because it is available in Cognito
# Lite; Managed Login v2 would require an Essentials/Plus feature tier.
resource "aws_cognito_user_pool_ui_customization" "web" {
  client_id    = aws_cognito_user_pool_client.web.id
  user_pool_id = aws_cognito_user_pool.pilot.id
  css          = ".banner-customizable { background-color: #0b2a3c; } .submitButton-customizable { background-color: #0b6b5b; }"

  depends_on = [aws_cognito_user_pool_domain.pilot]
}

# No hay aws_cognito_user: las cuatro identidades se crean post-deploy con
# valores sintéticos, se mapean sub -> tenant/rol en DynamoDB y reciben sus
# credenciales por un canal privado que nunca pasa por Git ni Terraform state.
