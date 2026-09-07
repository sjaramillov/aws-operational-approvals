terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  # El estado de este piloto es deliberadamente independiente del stack APPROVALS.
  # La configuración concreta del backend vive en backend.s3.hcl (ignorado).
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.expected_aws_account_id]

  assume_role {
    role_arn     = var.deployment_role_arn
    session_name = "approvals-sales-demo-${var.environment}"
  }

  default_tags {
    tags = local.common_tags
  }
}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}
