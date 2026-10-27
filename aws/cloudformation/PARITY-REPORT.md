# CloudFormation Parity Report

> **Target:** pure CloudFormation (no SAM: nothing to package), transform `AWS::LanguageExtensions` for `Fn::ForEach`
> **Stack:** `oecalc-shared-api-hostname-<env>` from `aws/cloudformation/template.yaml`
> **Terraform reference:** `modules/api-hostname` (main.tf, records.tf, monitoring.tf, ws.tf, variables.tf, outputs.tf)
> and `environments/{common,dev,prod}` (working tree of 2026-10-26, including the uncommitted `ws.tf`)
> **Date:** 2026-10-26

## 1. Summary

The template creates the same resources, with the same names, SSM contracts and alarms, as the Terragrunt unit
`environments/<env>/api-hostname`. Differences in mechanism (not in result) are listed in section 4.
**Only one tool may own an environment at a time**: both create the same names (section 5).

## 2. Inputs

| Terraform (env.hcl / root.hcl) | CloudFormation parameter | Where the value lives |
|---|---|---|
| `env` (`env_type`) | `EnvironmentType` (dev, qa, prod) | `parameters/<env>-us-east-2.json` |
| `context = "oecalc"` (root.hcl) | `Context` | parameters file |
| `replace(region, "-", "")` | `StrippedRegion` (`useast2`) | parameters file; the deploy script checks it matches the region |
| `web_domain_name` | `WebDomainName` | parameters file (dev: `...dev.nube-segura.com`, prod: `...nube-segura.com`) |
| `ws_host_enabled` | `WsHostEnabled` | parameters file (dev `true`, prod `false`) |
| `certificate_expiry_alert_days` | `CertificateExpiryAlertDays` (`30,7`) | parameters file |
| `zone_id` (ROUTE53_ZONE_ID) | `ZoneId` (`AWS::Route53::HostedZone::Id`) | deploy time only |
| `alert_email` (SUPPORT_EMAIL) | `AlertEmail` (NoEcho) | deploy time only |
| `backends` (API_WEIGHT_<NAME>) | `SlsWeight`, `EcsWeight`, `EksWeight`, `Ec2Weight` | deploy time only |
| `data.aws_ssm_parameters_by_path.backends` | `<Name>DnsName`, `<Name>HostedZoneId` (sls, ecs, eks, ec2, ws) | resolved from SSM by the deploy script |
| provider `default_tags` (5 mandatory tags) | stack tags (`--tags`) | deploy script |

## 3. Resource map

| Terraform address | CloudFormation logical ID | Type | Notes |
|---|---|---|---|
| `aws_acm_certificate.api` | `ApiCertificate` | `AWS::CertificateManager::Certificate` | `api.<domain>`, DNS, RSA_2048, tag `Name=acm-useast2-oecalc-api-<env>` |
| `aws_route53_record.validation["api..."]` | (owned by `ApiCertificate`) | - | `DomainValidationOptions.HostedZoneId`: CloudFormation writes the CNAME (section 4) |
| `aws_acm_certificate_validation.api` | (owned by `ApiCertificate`) | - | The resource completes only when the certificate is issued |
| `aws_ssm_parameter.certificate_arn` | `ApiCertificateArnParameter` | `AWS::SSM::Parameter` | `/oecalc/<env>/api-certificate-arn`, String, Standard, tag `Name` |
| `aws_sns_topic.alerts` | `AlertsTopic` | `AWS::SNS::Topic` | `sns-useast2-oecalc-api-alerts-<env>`, no customer key (same as Terraform) |
| `aws_sns_topic_policy.alerts` | `AlertsTopicPolicy` | `AWS::SNS::TopicPolicy` | `AllowCloudWatchAlarms` with `aws:SourceAccount = AWS::AccountId`; `DenyInsecureTransport` |
| `aws_sns_topic_subscription.alerts_email` | `AlertsEmailSubscription` | `AWS::SNS::Subscription` | protocol email |
| `aws_cloudwatch_metric_alarm.certificate_expiry["30"/"7"]` | `ApiCertExpiry{30,7}dAlarm` | `AWS::CloudWatch::Alarm` | `Fn::ForEach`; `alrm-useast2-oecalc-api-cert-expiry-<N>d-<env>`; ACM-03 (30 and 7 days, `LessThanOrEqualToThreshold`, Minimum, period 86400, one period, `notBreaching`); same in both tools |
| `terraform_data.weights_guard` | `Rules.WeightsGuard` | - | Also enforced by the deploy script before any AWS call |
| `aws_route53_record.api["sls"/"ecs"/"eks"/"ec2"]` | `ApiRecordSls`, `ApiRecordEcs`, `ApiRecordEks`, `ApiRecordEc2` | `AWS::Route53::RecordSet` | Weighted A alias, `SetIdentifier` = backend name, `EvaluateTargetHealth: false`; Condition: both registry values non-empty |
| `aws_acm_certificate.ws[0]` | `WsCertificate` | `AWS::CertificateManager::Certificate` | Condition `IsWsHostEnabled`; tag `Name=acm-useast2-oecalc-ws-<env>` |
| `aws_route53_record.ws_validation[...]`, `aws_acm_certificate_validation.ws[0]` | (owned by `WsCertificate`) | - | As for the api certificate |
| `aws_ssm_parameter.ws_certificate_arn[0]` | `WsCertificateArnParameter` | `AWS::SSM::Parameter` | `/oecalc/<env>/ws-certificate-arn`, Condition `IsWsHostEnabled` |
| `aws_cloudwatch_metric_alarm.ws_certificate_expiry[...]` | `WsCertExpiry{30,7}dAlarm` | `AWS::CloudWatch::Alarm` | Condition `IsWsHostEnabled`; same topic, no new subscription |
| `aws_route53_record.ws[0]` | `WsRecord` | `AWS::Route53::RecordSet` | Simple A alias; Condition `IsWsRecordEnabled` (switch on and both ws registry values present) |

Outputs: `certificate_arn` -> `CertificateArn`; `api_domain_name` -> `ApiDomainName`; `active_backends` (map) ->
`ActiveBackend<Name>Weight` (one per backend with a record); `ws_certificate_arn` -> `WsCertificateArn`;
`ws_domain_name` -> `WsDomainName` (both only when `WsHostEnabled`). No `Export`: consumers read the SSM contracts.

### Resource count per type (dev: ws on, every backend and ws published)

| Type | Terraform | CloudFormation | Difference |
|---|---|---|---|
| ACM certificate | 2 | 2 | - |
| ACM certificate validation | 2 | 0 | Built into the certificate resource |
| Route 53 record (validation CNAME) | 2 | 0 | Written by CloudFormation, not a stack resource |
| Route 53 record (api weighted + ws) | 5 | 5 | - |
| SSM parameter | 2 | 2 | - |
| SNS topic / policy / subscription | 1 / 1 / 1 | 1 / 1 / 1 | - |
| CloudWatch alarm | 8 | 8 | - |
| `terraform_data` | 1 | 0 | `Rules` section |

Prod (`WsHostEnabled=false`): the ws certificate, parameter, 2 alarms and record are absent in both tools.

## 4. Behavior differences and gaps

1. **Validation records.** Terraform declares the CNAMEs (`allow_overwrite = true`, TTL 300). CloudFormation
   creates them through `DomainValidationOptions.HostedZoneId`; they are not stack resources. The resource page
   does not document whether an existing CNAME is adopted or whether the record is removed on stack deletion
   (not verified). `destroy-dev.sh` lists any CNAME left for `api.`/`ws.` after a delete (read only).
2. **Subdomain precondition.** Terraform fails at plan time when `api.<domain>` is not inside the hosted zone.
   `Rules` cannot read the zone name, so the deploy script checks it (`route53 get-hosted-zone`) before the change
   set. `ZoneId` uses the type `AWS::Route53::HostedZone::Id`, which also checks that the zone exists.
3. **Backend registry.** Terraform reads `/oecalc/<env>/api-backends` recursively and skips missing values.
   CloudFormation cannot list by path nor skip a missing parameter, so the script resolves the values and passes
   them as parameters (empty = no record). A registry change is picked up only on the next deploy (same as the
   Terraform plan). The backend list (sls, ecs, eks, ec2) is fixed in the template: adding a backend means three
   parameters, one Condition and one record (Terraform: one `env.hcl` entry).
4. **Weights guard.** `Rules.WeightsGuard` compares strings: weights are `String` parameters restricted to the
   canonical integers 0-255 (`AllowedPattern`), so `"0"` is the only zero. The script normalizes the input
   exactly like `try(tonumber(x), 0)` and refuses non-integers or values above 255.
5. **create_before_destroy.** A certificate replacement in CloudFormation creates the new resource first and
   deletes the old one in the cleanup phase, which matches `create_before_destroy`. The deploy script refuses
   any replacement anyway (owner rule of `scripts/plan_guard.py`).
6. **Update behavior.** `DomainName`, `DomainValidationOptions`, `KeyAlgorithm` of the certificate, `Name` of the
   SSM parameter, `TopicName`, `AlarmName`, `Endpoint` of the subscription and `Name`/`HostedZoneId` of the records
   force replacement. Changing `SUPPORT_EMAIL` therefore replaces the subscription, refused by the guard, exactly
   as Terraform's plan guard refuses it. Source discrepancy: the template reference page says `ValidationMethod`
   updates with no interruption; the cfn-lint 1.48.1 schema lists it as create-only. The value never changes here.
7. **Delete protection.** Terraform has no `prevent_destroy` on this unit: no `DeletionPolicy: Retain` and no
   termination protection in the template. Prod protection relies on the change set guard; enabling stack
   termination protection on prod is an owner decision.
8. **Alarm thresholds.** `Fn::ForEach` over `CertificateExpiryAlertDays` needs `CAPABILITY_AUTO_EXPAND`. Changing the
   list adds or removes alarms. Owner decision 2026-10-26: ACM-03 is exactly 30 and 7 days (was 90/60/30/15 with
   `LessThanThreshold`), so existing deployments retire the 90, 60 and 15 day alarms. Both guards allow only that
   removal: `scripts/plan_guard.py` (pure delete of `certificate_expiry`/`ws_certificate_expiry` keys 90, 60, 15) and
   `lib/cfn_local.py changeset-guard` (`Api|WsCertExpiry{90,60,15}dAlarm`); the 30 and 7 day alarms and any other
   removal stay refused. The 30 day alarm changes operator in place (no replacement).
9. **Tags.** The 5 mandatory tags are stack tags (propagated). `Name` tags on the certificates and SSM parameters are
   in the template, like Terraform. Route 53 records and the subscription have no tags in either tool.

## 5. One tool per environment

Both tools create the same names (SSM parameters, SNS topic, alarm names, the same Route 53 records). Running both
in one environment fails (SSM `Name` and record conflicts) or, worse, two tools manage the same object. The local
deploy script stops when the Terraform state of `<env>/api-hostname` holds managed resources, and the destroy script
never touches a Terraform deployment. Switching = destroy with the current tool, then deploy with the other. Moving
live resources between tools (CloudFormation resource import, `terraform import`) is **not covered**; switching prod
without a migration plan is not supported (the backends depend on the certificate ARNs, which change).
**Lock (owner decision 2026-10-26).** The GitHub environment variable `IAC_TOOL` (`terraform` (the default when unset) or `cloudformation`) says which tool owns the environment. The Terraform workflows (`deploy.yml`, so Deploy DEV and Deploy
PROD, `drift-check.yml`, `destroy-dev.yml`) fail at the start, before any AWS call, when it is anything but
`terraform`; the local CloudFormation scripts refuse to run unless it is `cloudformation` (read from GitHub with the
organization token, or exported in the shell for offline use), in addition to the Terraform state check. No new AWS
permission is involved.

## 6. Verification

| Check | Result |
|---|---|
| `cfn-lint 1.48.1 --regions us-east-2` (also with `-I`) | No findings |
| YAML decode and `Fn::ForEach` expansion (cfn-lint) | 2 certificates, 2 SSM parameters, 5 records, topic, policy, subscription, 4 alarms |
| Parameter files JSON parse | OK |
| `deploy-dev.sh --dry-run` scenarios (missing zone, weights 0/invalid/over 255, published and unpublished backends, ws partial) | As expected |
| `aws cloudformation validate-template`, change set creation, real deployment | Not run (no AWS calls made) |

Sources: cfn-lint 1.48.1 bundled resource schemas (property names, create-only properties); AWS CloudFormation
Template Reference (`AWS::CertificateManager::Certificate`, `Fn::ForEach`) and User Guide (`Rules` section),
consulted 2026-10-26; Terraform AWS provider constraint `~> 6.0` (modules/api-hostname/versions.tf).
