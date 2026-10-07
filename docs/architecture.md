# Architecture

The API edge of the calculator: the public host name `api.<web domain>`, its certificate, the DNS weights that
decide which backend answers, and the certificate expiry alarms. One Terragrunt unit (`api-hostname`) per
environment (`dev`, `prod`), each in its own AWS account, region `us-east-2`.

Diagram: `docs/architecture.drawio`.

## Components

| Component | Role |
|---|---|
| Regional ACM certificate | TLS for `api.<web domain>`, DNS-validated (automatic renewal), RSA 2048 |
| Route 53 validation record | CNAME that proves ownership of the host name (written with overwrite) |
| SSM parameter `api-certificate-arn` | Contract with the backends: they read the certificate ARN from it |
| Weighted Route 53 alias records | One `A` alias per backend that published its target; the weight (0-255) splits the traffic |
| Backend registry (SSM, read only) | `/oecalc/<env>/api-backends/<name>/{dns-name,hosted-zone-id}`, published by each backend |
| SNS topic, policy and email subscription | Receives the expiry alarms; the policy allows CloudWatch to publish and denies insecure transport |
| 4 CloudWatch alarms | `DaysToExpiry` of the certificate below 90, 60, 30 and 15 days |
| Weights guard (`terraform_data`) | Refuses a plan where every published backend has weight 0 |

## Resource inventory

Naming: `<acronym>-<region code>-<context>[-<descriptor>]-<env>` with region code `useast2` and context `oecalc`.
Names are identical in `dev` and `prod` except for the last segment and the domain.

| Resource type | Name or identifier (per environment) | Notes |
|---|---|---|
| `aws_acm_certificate.api` | domain `api.<web domain>`; tag `Name = acm-useast2-oecalc-api-<env>` | `create_before_destroy`; precondition: host name inside the hosted zone |
| `aws_route53_record.validation` | name and value given by ACM (CNAME, TTL 300) | `allow_overwrite = true`; destroy only when no other certificate uses it |
| `aws_acm_certificate_validation.api` | no AWS object | waits for issuance |
| `aws_ssm_parameter.certificate_arn` | `/oecalc/<env>/api-certificate-arn` (String, Standard) | tag `Name = ssm-useast2-oecalc-api-certificate-arn-<env>` |
| `aws_route53_record.api["<backend>"]` | `api.<web domain>`, type `A`, set identifier `<backend>` | one per backend among `sls`, `ecs`, `eks`, `ec2` that published its target; alias, target health evaluation off |
| `terraform_data.weights_guard` | no AWS object | precondition on the sum of weights |
| `aws_sns_topic.alerts` | `sns-useast2-oecalc-api-alerts-<env>` | no customer key (an AWS managed key would block CloudWatch) |
| `aws_sns_topic_policy.alerts` | same topic | statements `AllowCloudWatchAlarms` (source account condition) and `DenyInsecureTransport` |
| `aws_sns_topic_subscription.alerts_email` | email endpoint from `SUPPORT_EMAIL` | the recipient must confirm it |
| `aws_cloudwatch_metric_alarm.certificate_expiry["90"/"60"/"30"/"15"]` | `alrm-useast2-oecalc-api-cert-expiry-<days>d-<env>` | `AWS/CertificateManager` `DaysToExpiry`, minimum, period 1 day, missing data not breaching |

Tags on every resource (provider default tags from `environments/root.hcl`): `team-owner = nube-segura`,
`project-name = over-engineered-calculator`, `app-name = calc-shared`,
`repo-name = over-engineered-simple-calculator-shared-resources`, `env-type = <env>`.

Differences between `dev` and `prod`: only `env.hcl` (environment name, web domain `...dev.nube-segura.com` versus
`...nube-segura.com`, placeholder email default) and the GitHub environment values (role, account, hosted zone,
weights, email). Both use region `us-east-2`. State bucket per environment: `bckt-useast2-tf-state-<env>-<account-id>`;
state key `over-engineered-simple-calculator-shared-resources/<env>/api-hostname/terraform.tfstate`.

Outputs: `certificate_arn`, `api_domain_name`, `active_backends` (name to weight).

## Flows

1. **Certificate:** Terraform requests the certificate, writes the validation CNAME in the hosted zone, waits for
   issuance, then publishes the ARN in SSM. Each backend reads that parameter and attaches the certificate to its
   own custom domain.
2. **Registration:** each backend publishes the DNS name and hosted zone ID of its endpoint under
   `/oecalc/<env>/api-backends/<name>/`. At plan time this repository reads that path; a registered backend with both
   parameters gets a weighted alias record.
3. **Traffic:** a client resolves `api.<web domain>`; Route 53 answers with one alias according to the weights.
4. **Alerts:** CloudWatch alarms on `DaysToExpiry` publish to the SNS topic, which emails `SUPPORT_EMAIL`.
5. **Switching:** change the GitHub variable `API_WEIGHT_<NAME>` and run Deploy DEV or Deploy PROD by hand. No other
   repository deploys.

## Deployment order across repositories

1. Account foundation (deployment roles, state bucket, hosted zone), outside these repositories.
2. This repository: certificate, SSM parameter and alarms (with no published backend there are no records).
3. Backends (`sls`, `ecs`, later `eks`, `ec2`): read the certificate ARN, create their custom domain, publish their target.
4. This repository again (by hand or by a push): creates the weighted record of the new backend.
5. Web page and Cognito (`over-engineered-simple-calculator-webpage`): uses the host name only.

## Trust boundaries

- Internet to Route 53 and the backend endpoints: public; this repository exposes only DNS names.
- GitHub Actions to AWS: OpenID Connect, role from the secret `ROLE_ARN` of the GitHub environment. The workflow checks
  that the credentials belong to the account in `AWS_ACCOUNT_ID` and that the branch matches the environment
  (`develop` to `dev`, `main` to `prod`).
- Between repositories: only SSM parameters (certificate ARN written here, backend targets written by the backends);
  no shared state, no cross-account access.
- The plan guard refuses deletes and replacements: the certificate, the records and the alarms cannot disappear by a
  routine deployment.
- The SNS topic accepts publishes only from CloudWatch of the same account and only over TLS.
