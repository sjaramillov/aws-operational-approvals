resource "aws_s3_bucket" "web" {
  #checkov:skip=CKV_AWS_18:S3 access logs would collect requester metadata for a synthetic eight-day pilot; CloudFront metrics plus immutable artifact hashes are the bounded evidence.
  #checkov:skip=CKV_AWS_144:Cross-region replication is intentionally excluded for this ephemeral, rebuildable static bundle.
  #checkov:skip=CKV_AWS_145:SSE-S3 encrypts the non-sensitive public bundle; using the guardrail CMK would require broadening its key policy to CloudFront OAC.
  #checkov:skip=CKV2_AWS_62:Object notifications add no business or expiry control to the immutable frontend bundle.
  bucket        = "${local.resource_prefix}-web-${substr(sha256(var.expected_aws_account_id), 0, 12)}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket = aws_s3_bucket.web.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "web" {
  bucket = aws_s3_bucket.web.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "web" {
  bucket = aws_s3_bucket.web.id

  rule {
    id     = "bound-pilot-artifacts"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.web]
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.resource_prefix}-oac"
  description                       = "OAC exclusivo del bucket privado del piloto."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_cache_policy" "web" {
  name        = "${local.resource_prefix}-bounded-cache"
  comment     = "Cache breve para el PWA efímero."
  default_ttl = 300
  min_ttl     = 0
  max_ttl     = 3600

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name    = "${local.resource_prefix}-security-headers"
  comment = "CSP y cabeceras defensivas del PWA sintético."

  security_headers_config {
    content_security_policy {
      content_security_policy = join(" ", [
        "default-src 'self';",
        "base-uri 'self';",
        "object-src 'none';",
        "frame-ancestors 'none';",
        "form-action 'self' https://${aws_cognito_user_pool_domain.pilot.domain}.auth.${var.aws_region}.amazoncognito.com;",
        "connect-src 'self' ${aws_apigatewayv2_api.sales.api_endpoint} https://${aws_cognito_user_pool_domain.pilot.domain}.auth.${var.aws_region}.amazoncognito.com;",
        "img-src 'self' data:;",
        "script-src 'self';",
        "style-src 'self';",
        "font-src 'self';",
        "manifest-src 'self';",
        "worker-src 'self';"
      ])
      override = true
    }

    content_type_options { override = true }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }

  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
      override = true
    }

    items {
      header   = "X-APPROVALS-Demo"
      value    = "synthetic-data-no-real-pii"
      override = true
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  #checkov:skip=CKV_AWS_68:WAF is outside the bounded pilot; API throttling is best effort and the DynamoDB 10-per-tenant/day transaction is the hard business-write limit.
  #checkov:skip=CKV_AWS_86:Standard logs expose viewer IP metadata and require another bucket; the no-PII pilot uses aggregate CloudFront metrics instead.
  #checkov:skip=CKV_AWS_174:The CloudFront default certificate is explicitly constrained to TLSv1.2_2021; this check cannot resolve the default-certificate branch.
  #checkov:skip=CKV_AWS_310:Origin failover is not cost-justified for an eight-day rebuildable static demo and no SLA is claimed.
  #checkov:skip=CKV_AWS_374:The approved evaluator pilot has no geographic restriction requirement; authentication and tenant authorization are the access controls.
  #checkov:skip=CKV2_AWS_42:The pilot intentionally uses the CloudFront default certificate and no custom domain, eliminating certificate/domain lifecycle.
  #checkov:skip=CKV2_AWS_47:No WAF is attached by approved scope; the static React bundle does not run Log4j and business writes have a DynamoDB hard quota.
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "Aprobaciones operativas; piloto sintético y efímero."
  price_class         = "PriceClass_100"
  wait_for_deployment = true
  retain_on_delete    = false

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "private-s3-web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id           = "private-s3-web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.web.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
    compress                   = true
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  depends_on = [
    aws_s3_bucket_public_access_block.web,
    aws_s3_bucket_ownership_controls.web,
    aws_s3_bucket_server_side_encryption_configuration.web
  ]
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid       = "AllowOnlyThisCloudFrontDistribution"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = data.aws_iam_policy_document.web_bucket.json
}
