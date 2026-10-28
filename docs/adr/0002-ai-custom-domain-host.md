# ADR 0002: Dedicated `ai` custom domain host for the AI backend

- Status: accepted (2026-10-28)

## Context

The AI backend (`over-engineered-simple-calculator-ai-version`) requires a public custom domain `ai.<web domain>` in `dev` to expose its REST API endpoint alongside `api.<web domain>` and `ws.<web domain>`.

Under the project architecture (ADR 0001), backends own their application-level custom domain resource in API Gateway, while this repository (`over-engineered-simple-calculator-shared-resources`) owns the shared public DNS zone, regional ACM certificates, certificate expiry alarms, and Route 53 alias records.

The `dev` environment is currently owned and managed exclusively by CloudFormation (`oecalc-shared-api-hostname-dev`, locked via `IAC_TOOL=cloudformation`). Furthermore, while the custom domain is disabled or not yet created in the AI backend, the backend publishes the literal sentinel string `none` in its SSM target parameters `/oecalc/<env>/api-backends/ai/dns-name` and `/oecalc/<env>/api-backends/ai/hosted-zone-id`.

## Decision

1. **Dedicated ACM Certificate:** Provision a dedicated regional ACM certificate (`AiCertificate`) for `ai.<web domain>` with Route 53 DNS validation. Its identifier is published to `/oecalc/<env>/ai-certificate-arn` (Standard String) only after certificate issuance.
2. **Dedicated Expiry Alarms:** Create two CloudWatch alarms (`AiCertExpiry30dAlarm`, `AiCertExpiry7dAlarm`) monitoring `DaysToExpiry` at 30 and 7 days, publishing to the existing alerts SNS topic (`AlertsTopic`) without creating new topics or subscriptions.
3. **Simple Route 53 Alias Record:** Route `ai.<web domain>` via a simple `A` alias (`AiRecord`) pointing to the regional API Gateway endpoint target with `EvaluateTargetHealth: false`. The host is never weighted and is strictly isolated from the weighted API backends (`sls`, `ecs`, `eks`, `ec2`) and the `WeightsGuard`.
4. **Double `none` Sentinel Guard:** Protect Route 53 from invalid record creation while the AI backend custom domain is disabled:
   - **Template condition:** `IsAiRecordEnabled` requires `AiHostEnabled` to be true and both `AiDnsName` and `AiHostedZoneId` to be non-empty and not equal to `'none'`.
   - **Deploy helper (`cfn_local.py`):** Treats `none` (case-insensitive, trimmed) and empty values as unpublished, validates target syntax if populated, and permits safe retirement of `AiRecord` in the changeset guard.
5. **CloudFormation-only Implementation (Deviation D1):** Implement the `ai` host exclusively in `aws/cloudformation/template.yaml` and parameter files (`dev`: `true`, `prod`: `false`). Leave the Terraform twin (`modules/api-hostname`) intentionally untouched (accepted deviation D1), as `dev` is managed solely by CloudFormation. A follow-up decision will determine whether to build the Terraform twin or deprecate the Terraform twin for this stack.

## Consequences

- **Decoupled Deployment:** The circular dependency between repositories is broken using a 4-step deployment sequence: shared resources certificate deployed first (step 1), AI backend domain created second (step 2), shared resources DNS record deployed third (step 3), followed by verification (step 4).
- **Safe Sentinel Handling:** The backend safely publishes `none` before custom domain enablement without causing stack deployment failures or orphan DNS records.
- **Independent Failure Domain:** The `ai` host, certificate, and alarms do not touch or impact routing for `api.<web domain>` or `ws.<web domain>`.
- **Terraform Parity Gap (Deviation D1):** The Terraform module does not define `ai` resources. If `dev` is ever switched back to Terraform (`IAC_TOOL=terraform`), the `ai` host resources would be unrecognized and would need to be migrated, imported, or recreated.
