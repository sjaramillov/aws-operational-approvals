resource "aws_apigatewayv2_api" "sales" {
  name          = "${local.resource_prefix}-http"
  description   = "Bounded synthetic APPROVALS sales and Servicio API."
  protocol_type = "HTTP"

  cors_configuration {
    allow_credentials = false
    allow_headers     = ["authorization", "content-type", "idempotency-key"]
    allow_methods     = ["GET", "POST", "OPTIONS"]
    # OAuth redirects remain exact and no cookies/credentials cross origins.
    # The browser sends a scoped bearer token; the CloudFront CSP below binds
    # this PWA to this exact API and Cognito domain without a Terraform cycle.
    allow_origins  = ["*"]
    expose_headers = ["x-request-id"]
    max_age        = 300
  }
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.sales.id
  name             = "cognito-jwt"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.web.id]
    issuer   = "https://${aws_cognito_user_pool.pilot.endpoint}"
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.sales.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 10000
}

resource "aws_apigatewayv2_route" "routes" {
  #checkov:skip=CKV_AWS_309:GET /health is intentionally anonymous and side-effect free; every business route in the same for_each explicitly uses the Cognito JWT authorizer.
  for_each = local.routes

  api_id    = aws_apigatewayv2_api.sales.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.api.id}"

  authorization_type = each.value.authorization_type
  authorizer_id      = each.value.authorization_type == "JWT" ? aws_apigatewayv2_authorizer.cognito.id : null
  authorization_scopes = (
    each.value.authorization_type == "JWT" ? ["openid"] : null
  )
}

resource "aws_apigatewayv2_stage" "default" {
  #checkov:skip=CKV_AWS_76:An account-wide API Gateway Logs role would violate this stack's isolation; Lambda and Step Functions retain allowlisted operational logs for 14 days without request bodies, claims or task tokens.
  api_id      = aws_apigatewayv2_api.sales.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 10
    throttling_rate_limit    = 5
  }

  dynamic "route_settings" {
    for_each = local.write_route_keys

    content {
      route_key                = route_settings.value
      detailed_metrics_enabled = true
      throttling_burst_limit   = 2
      throttling_rate_limit    = 1
    }
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id   = "AllowOnlySalesHttpApi"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.api.function_name
  principal      = "apigateway.amazonaws.com"
  source_arn     = "${aws_apigatewayv2_api.sales.execution_arn}/*/*"
  source_account = var.expected_aws_account_id
}
