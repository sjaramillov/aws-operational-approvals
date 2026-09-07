mock_provider "aws" {
  override_during = plan

  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }

  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }

  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }

  mock_resource "aws_iam_policy" {
    defaults = { arn = "arn:aws:iam::000000000000:policy/mock-sales-policy" }
  }

  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock-sales-role" }
  }

  mock_resource "aws_cognito_user_pool" {
    defaults = {
      arn      = "arn:aws:cognito-idp:us-east-1:000000000000:userpool/mock"
      endpoint = "cognito-idp.us-east-1.amazonaws.com/mock"
      id       = "us-east-1_mock"
    }
  }

  mock_resource "aws_cloudfront_distribution" {
    defaults = {
      arn         = "arn:aws:cloudfront::000000000000:distribution/MOCK"
      domain_name = "mock.cloudfront.net"
      id          = "MOCK"
    }
  }

  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:us-east-1:000000000000:table/approvals-sales-demo-records" }
  }

  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:us-east-1:000000000000:function:mock"
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/mock/invocations"
    }
  }

  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      api_endpoint  = "https://mock.execute-api.us-east-1.amazonaws.com"
      execution_arn = "arn:aws:execute-api:us-east-1:000000000000:mock"
      id            = "mock"
    }
  }
}

run "bounded_sales_demo_contract" {
  command = plan

  variables {
    aws_region                                   = "us-east-1"
    expected_aws_account_id                      = "000000000000"
    deployment_role_arn                          = "arn:aws:iam::000000000000:role/APPROVALS-TerraformDeploymentRole"
    deployment_role_contract_sha256              = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    source_revision                              = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    deployment_profile                           = "sales_demo"
    application_kms_key_arn                      = "arn:aws:kms:us-east-1:000000000000:key/abcdefab-cdef-abcd-efab-cdefabcdefab"
    application_role_permissions_boundary_arn    = "arn:aws:iam::000000000000:policy/approvals-sales-demo-application-boundary"
    application_role_permissions_boundary_sha256 = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    guardrail_budget_name                        = "approvals-central-demo-account-guardrail"
    guardrail_budget_limit_usd                   = 20
    api_lambda_package_path                      = "/private/artifacts/sales-api.zip"
    api_lambda_source_code_sha256                = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    worker_lambda_package_path                   = "/private/artifacts/sales-worker.zip"
    worker_lambda_source_code_sha256             = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    frontend_release_sha256                      = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  }

  assert {
    condition     = terraform_data.deployment_contract.input.deployment_profile == "sales_demo"
    error_message = "The isolated root module must remain pinned to sales_demo."
  }

  assert {
    condition = (
      strcontains(
        aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy,
        aws_apigatewayv2_api.sales.api_endpoint
      ) &&
      strcontains(
        aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy,
        aws_cognito_user_pool_domain.pilot.domain
      ) &&
      !strcontains(
        aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy,
        "https://*."
      )
    )
    error_message = "CloudFront CSP must bind the exact API and Cognito targets without host wildcards."
  }

  assert {
    condition = (
      jsondecode(aws_dynamodb_table_item.pilot_config.item).status.S == "PREPARED" &&
      jsondecode(aws_dynamodb_table_item.pilot_config.item).expires_at_epoch.N == "0" &&
      aws_scheduler_schedule.expiry.state == "DISABLED"
    )
    error_message = "Terraform must deploy PREPARED and leave T0/expiry to the post-readiness activator."
  }

  assert {
    condition = alltrue([
      for role in [aws_iam_role.api, aws_iam_role.worker, aws_iam_role.workflow, aws_iam_role.scheduler] :
      role.permissions_boundary == "arn:aws:iam::000000000000:policy/approvals-sales-demo-application-boundary"
    ])
    error_message = "All runtime roles must use the external guardrails-owned boundary."
  }

  assert {
    condition = (
      aws_dynamodb_table.sales.billing_mode == "PROVISIONED" &&
      aws_dynamodb_table.sales.read_capacity == 5 &&
      aws_dynamodb_table.sales.write_capacity == 5
    )
    error_message = "The single table must remain provisioned at 5 RCU/5 WCU."
  }

  assert {
    condition = (
      aws_lambda_function.api.runtime == "python3.12" &&
      aws_lambda_function.api.memory_size == 256 &&
      aws_lambda_function.api.timeout == 10 &&
      aws_lambda_function.worker.runtime == "python3.12" &&
      aws_lambda_function.worker.memory_size == 256 &&
      aws_lambda_function.worker.timeout == 10
    )
    error_message = "Exactly two Python 3.12 Lambdas must stay at 256 MB/10 seconds."
  }

  assert {
    condition = (
      aws_cognito_user_pool.pilot.admin_create_user_config[0].allow_admin_create_user_only &&
      aws_cognito_user_pool.pilot.user_pool_tier == "LITE" &&
      aws_cognito_user_pool_domain.pilot.managed_login_version == 1 &&
      length(aws_cognito_user_pool_ui_customization.web.css) > 0 &&
      !aws_cognito_user_pool_client.web.generate_secret &&
      aws_cognito_user_pool_client.web.allowed_oauth_flows == toset(["code"]) &&
      aws_cognito_user_pool_client.web.explicit_auth_flows == toset(["ALLOW_USER_SRP_AUTH"]) &&
      aws_cognito_user_pool_client.web.access_token_validity == 15 &&
      aws_cognito_user_pool_client.web.token_validity_units[0].access_token == "minutes" &&
      aws_cognito_user_pool_client.web.refresh_token_validity == 8 &&
      aws_cognito_user_pool_client.web.token_validity_units[0].refresh_token == "days" &&
      aws_cognito_user_pool_client.web.refresh_token_rotation[0].feature == "ENABLED" &&
      aws_cognito_user_pool_client.web.refresh_token_rotation[0].retry_grace_period_seconds == 10
    )
    error_message = "Cognito must disable self-signup and use a public authorization-code client."
  }

  assert {
    condition = (
      aws_apigatewayv2_stage.default.default_route_settings[0].throttling_rate_limit == 5 &&
      aws_apigatewayv2_stage.default.default_route_settings[0].throttling_burst_limit == 10
    )
    error_message = "HTTP API must keep the 5/10 default throttling targets."
  }

  assert {
    condition     = aws_sfn_state_machine.application.type == "STANDARD"
    error_message = "The approval workflow must remain Standard."
  }

  assert {
    condition = (
      aws_cloudwatch_log_group.api.retention_in_days == 14 &&
      aws_cloudwatch_log_group.worker.retention_in_days == 14 &&
      aws_cloudwatch_log_group.workflow.retention_in_days == 14
    )
    error_message = "All workload log groups must retain events for 14 days."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.web.block_public_acls &&
      aws_s3_bucket_public_access_block.web.block_public_policy &&
      aws_s3_bucket_public_access_block.web.restrict_public_buckets &&
      aws_cloudfront_origin_access_control.web.signing_behavior == "always"
    )
    error_message = "The frontend origin must stay private behind an always-signing OAC."
  }

  assert {
    condition = (
      output.post_deploy_user_provisioning_contract.expected_user_count == 4 &&
      output.post_deploy_user_provisioning_contract.credential_rule == "private_channel_only_never_git_or_terraform_state"
    )
    error_message = "Exactly four synthetic post-deploy user slots must be declared without credentials."
  }

}
