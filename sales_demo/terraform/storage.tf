resource "aws_dynamodb_table" "sales" {
  #checkov:skip=CKV2_AWS_16:Provisioned 5 RCU/5 WCU is the approved deterministic cost ceiling; autoscaling would remove that fixed bound.
  name         = local.table_name
  billing_mode = "PROVISIONED"
  hash_key     = "pk"
  range_key    = "sk"

  read_capacity  = 5
  write_capacity = 5

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  #checkov:skip=CKV_AWS_28:PITR creates retained system backups after table deletion; the eight-day zero-PII pilot prioritizes a verifiable inventory-zero teardown and keeps reproducible synthetic fixtures instead.
  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.application_kms_key_arn
  }

  ttl {
    attribute_name = "ttl_epoch"
    enabled        = true
  }

  # Stack efímero: la protección real es el plan/destroy revisado y el backup
  # de evidencia sanitizada. Activarla impediría cumplir el cierre en una hora.
  deletion_protection_enabled = false
}

resource "aws_dynamodb_table_item" "pilot_config" {
  table_name = aws_dynamodb_table.sales.name
  hash_key   = aws_dynamodb_table.sales.hash_key
  range_key  = aws_dynamodb_table.sales.range_key

  item = jsonencode({
    pk                       = { S = "PILOT#${local.pilot_id}" }
    sk                       = { S = "META" }
    status                   = { S = "PREPARED" }
    expires_at_epoch         = { N = "0" }
    max_applications_per_day = { N = "10" }
    data_classification      = { S = "SYNTHETIC_ONLY" }
  })

  # La activación post-readiness hace PREPARED -> ACTIVE de forma condicional y
  # fija T0+192h. Terraform nunca activa ni extiende esa ventana.
  lifecycle {
    ignore_changes = [item]
  }
}
