resource "aws_scheduler_schedule" "expiry" {
  #checkov:skip=CKV_AWS_297:The one-time payload contains only an operation, public expiry timestamp and count; no secret or PII justifies expanding the persistent CMK key policy to Scheduler.
  name                         = local.schedule_name
  description                  = "Redundant expiry placeholder; activation sets the exact T0+192h schedule."
  schedule_expression          = "at(2099-01-01T00:00:00)"
  schedule_expression_timezone = "UTC"
  action_after_completion      = "DELETE"
  state                        = "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.worker.arn
    role_arn = aws_iam_role.scheduler.arn
    input = jsonencode({
      operation            = "EXPIRE_PILOT"
      scheduled_expires_at = "2099-01-01T00:00:00Z"
      expected_user_count  = 4
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }

  depends_on = [aws_iam_role_policy_attachment.scheduler]

  # activate_pilot.py owns these fields after readiness. Terraform must not
  # move T0, re-enable an expired pilot or overwrite the activated payload.
  lifecycle {
    ignore_changes = [schedule_expression, state, target[0].input]
  }
}
