# DEV environment configuration. Every difference between environments lives in
# env.hcl; the units and common are identical for dev and prod. Each environment is
# deployed to its own AWS account: the GitHub environment of the branch decides which
# role (and therefore which account) the deployment assumes.
locals {
  env        = "dev"
  aws_region = "us-east-2"

  # Local (untracked) secrets.yml next to this file, used only when running Terragrunt
  # by hand; the deploy workflow passes the values as environment variables.
  local_secrets = try(yamldecode(file("${dirname(find_in_parent_folders("env.hcl"))}/secrets.yml")), {})

  # --- Web domain ---
  # The hosted zone (GitHub environment variable ROUTE53_ZONE_ID) must live in this
  # environment's account. The API host name is api.<web_domain_name>.
  web_domain_name = "over-engineered-simple-calculator.dev.nube-segura.com"
  route53_zone_id = get_env("ROUTE53_ZONE_ID", try(local.local_secrets.ROUTE53_ZONE_ID, ""))

  # --- Alerts ---
  # Certificate expiry alarms. GitHub environment secret SUPPORT_EMAIL.
  alert_email = get_env("SUPPORT_EMAIL", try(local.local_secrets.SUPPORT_EMAIL, "alerts-dev@example.com"))

  # --- API backends ---
  # Routing weights (0-255) of the backends behind api.<web_domain_name>, from the GitHub
  # environment variables API_WEIGHT_<NAME>. An unset or non-numeric variable counts as 0; a
  # backend that has not published its target gets no record; the module refuses a plan where
  # every published backend has weight 0. To add a backend: one entry here and one variable
  # in the deploy workflow.
  api_backends = {
    sls = try(tonumber(get_env("API_WEIGHT_SLS", try(local.local_secrets.API_WEIGHT_SLS, "0"))), 0)
    ecs = try(tonumber(get_env("API_WEIGHT_ECS", try(local.local_secrets.API_WEIGHT_ECS, "0"))), 0)
    eks = try(tonumber(get_env("API_WEIGHT_EKS", try(local.local_secrets.API_WEIGHT_EKS, "0"))), 0)
    ec2 = try(tonumber(get_env("API_WEIGHT_EC2", try(local.local_secrets.API_WEIGHT_EC2, "0"))), 0)
  }
}
