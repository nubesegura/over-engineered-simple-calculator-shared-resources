# ---------------------------------------------------------
# TEMPORARY. Adoption of the resources that the webpage repository created (spec 001-move-api-edge).
# Import blocks are idempotent: once a resource is in this state they do nothing. Remove this file
# after the migration of every environment (it reads the existing certificate parameter, so it does
# not work in an account where nothing was created before).
#
# Not imported on purpose:
#   - the DNS validation record: created by overwrite (allow_overwrite, same name, type and value);
#   - aws_acm_certificate_validation and terraform_data.weights_guard: no AWS object behind them;
#   - the email subscription: created again; SNS returns the existing subscription when the
#     endpoint is already confirmed. Check afterwards that the topic has exactly one.
# ---------------------------------------------------------
data "aws_ssm_parameter" "adopted_certificate_arn" {
  name = "/${var.context}/${var.env_type}/api-certificate-arn"
}

locals {
  adopted_topic_arn = "arn:aws:sns:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:sns-${local.name_mid}-api-alerts-${var.env_type}"
}

import {
  to = aws_acm_certificate.api
  id = data.aws_ssm_parameter.adopted_certificate_arn.value
}

import {
  to = aws_ssm_parameter.certificate_arn
  id = "/${var.context}/${var.env_type}/api-certificate-arn"
}

import {
  to = aws_sns_topic.alerts
  id = local.adopted_topic_arn
}

import {
  to = aws_sns_topic_policy.alerts
  id = local.adopted_topic_arn
}

import {
  for_each = toset([for days in var.certificate_expiry_alert_days : tostring(days)])
  to       = aws_cloudwatch_metric_alarm.certificate_expiry[each.key]
  id       = "alrm-${local.name_mid}-api-cert-expiry-${each.key}d-${var.env_type}"
}

# Only the backends whose records already exist in AWS (created by the webpage repository). A backend
# that publishes its target later gets its record created, not imported.
import {
  for_each = toset([for name in ["sls", "ecs"] : name if contains(keys(local.active_backends), name)])
  to       = aws_route53_record.api[each.key]
  id       = "${var.zone_id}_${local.api_domain_name}_A_${each.key}"
}
