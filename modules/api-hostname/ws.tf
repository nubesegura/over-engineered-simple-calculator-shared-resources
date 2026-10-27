# ---------------------------------------------------------
# WebSocket host (ws.<web domain>): its own regional ACM certificate validated in Route 53,
# a pointer to it in SSM and two expiry alarms. Separate from the api host on purpose: the
# api certificate, records and alarms are not touched. Everything here is behind
# var.ws_host_enabled (on in dev, off in prod).
# ---------------------------------------------------------
locals {
  ws_domain_name = "ws.${var.web_domain_name}"
  ws_count       = var.ws_host_enabled ? 1 : 0
}

resource "aws_acm_certificate" "ws" {
  count = local.ws_count

  domain_name       = local.ws_domain_name
  validation_method = "DNS" # Automatic renewal (ACM-01)
  key_algorithm     = "RSA_2048"

  tags = {
    Name = "acm-${local.name_mid}-ws-${var.env_type}"
  }

  lifecycle {
    create_before_destroy = true

    # Fail at plan time instead of hanging on DNS validation.
    precondition {
      condition     = endswith(local.ws_domain_name, ".${trimsuffix(data.aws_route53_zone.web.name, ".")}")
      error_message = "The WebSocket hostname ${local.ws_domain_name} must be a subdomain of the hosted zone ${data.aws_route53_zone.web.name} (check ROUTE53_ZONE_ID for this environment's account)."
    }
  }
}

resource "aws_route53_record" "ws_validation" {
  for_each = {
    for option in(var.ws_host_enabled ? aws_acm_certificate.ws[0].domain_validation_options : toset([])) :
    option.domain_name => option
  }

  zone_id         = var.zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "ws" {
  count = local.ws_count

  certificate_arn         = aws_acm_certificate.ws[0].arn
  validation_record_fqdns = [for record in aws_route53_record.ws_validation : record.fqdn]
}

# Contract with the WebSocket repository: it reads the ARN from this parameter. The value comes
# from the validation resource, so the parameter exists only once the certificate is issued.
# A certificate ARN is an identifier, not a secret: plain String, standard tier.
resource "aws_ssm_parameter" "ws_certificate_arn" {
  count = local.ws_count

  name        = "/${var.context}/${var.env_type}/ws-certificate-arn"
  description = "ARN of the regional ACM certificate for ${local.ws_domain_name}"
  type        = "String"
  tier        = "Standard"
  value       = aws_acm_certificate_validation.ws[0].certificate_arn

  tags = {
    Name = "ssm-${local.name_mid}-ws-certificate-arn-${var.env_type}"
  }
}

# Expiry alarms (ACM-03), same topic as the api alarms; no new subscription.
resource "aws_cloudwatch_metric_alarm" "ws_certificate_expiry" {
  for_each = var.ws_host_enabled ? toset([for days in var.certificate_expiry_alert_days : tostring(days)]) : toset([])

  alarm_name          = "alrm-${local.name_mid}-ws-cert-expiry-${each.value}d-${var.env_type}"
  alarm_description   = "Certificate of ${local.ws_domain_name} expires in ${each.value} days or less. DNS-validated certificates renew by themselves: check the validation CNAME if this does not clear."
  namespace           = "AWS/CertificateManager"
  metric_name         = "DaysToExpiry"
  dimensions          = { CertificateArn = aws_acm_certificate.ws[0].arn }
  statistic           = "Minimum"
  period              = 86400
  evaluation_periods  = 1
  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = tonumber(each.value)
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]

  depends_on = [aws_acm_certificate_validation.ws]
}

# ---------------------------------------------------------
# Record ws.<web domain>: one simple A alias (no weight, no set identifier) to the WebSocket
# backend. The backend publishes, in this account and region:
#   /<context>/<env>/api-backends/ws/dns-name
#   /<context>/<env>/api-backends/ws/hosted-zone-id
# The record exists only when the switch is on and both parameters exist; with one or none the
# plan simply adds nothing. The parameters are read by the recursive lookup of records.tf
# (local.published), which does not fail when a path is missing. The key `ws` is rejected in
# var.backends, so this record never joins the weighted api set or its weights guard.
# evaluate_target_health is false for the same reason as in records.tf.
# Address kept in sync with WS_RECORD_ADDRESS in scripts/plan_guard.py.
# ---------------------------------------------------------
locals {
  ws_backend_dns_name       = try(local.published["${local.registry_prefix}/ws/dns-name"], "")
  ws_backend_hosted_zone_id = try(local.published["${local.registry_prefix}/ws/hosted-zone-id"], "")
  ws_record_count           = var.ws_host_enabled && local.ws_backend_dns_name != "" && local.ws_backend_hosted_zone_id != "" ? 1 : 0
}

resource "aws_route53_record" "ws" {
  count = local.ws_record_count

  zone_id = var.zone_id
  name    = local.ws_domain_name
  type    = "A"

  alias {
    name                   = local.ws_backend_dns_name
    zone_id                = local.ws_backend_hosted_zone_id
    evaluate_target_health = false
  }
}
