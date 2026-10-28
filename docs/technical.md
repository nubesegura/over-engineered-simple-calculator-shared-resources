# Technical guide

## Layout

| Path | Content |
|---|---|
| `modules/api-hostname/` | `main.tf` (certificate, validation, SSM), `records.tf` (backend registry, weights guard, records), `monitoring.tf` (topic, policy, subscription, alarms), `variables.tf`, `outputs.tf`, `versions.tf` (the adoption file is kept only as a reference in `docs/migrations/001-move-api-edge/imports.tf.example`) |
| `environments/root.hcl` | Provider with default tags, S3 remote state, `context = oecalc` |
| `environments/common/api-hostname.hcl` | Unit shared by both environments |
| `environments/{dev,prod}/env.hcl` | Region, web domain, hosted zone, alert email, weights |
| `environments/{dev,prod}/api-hostname/terragrunt.hcl` | Includes `root.hcl` and the common unit |
| `.github/` | `ci.yml`, `deploy.yml` (reusable), `deploy-dev.yml`, `deploy-prod.yml`, `actions/setup-iac`, `dependabot.yml` |

Versions: Terraform `~> 1.9` (CI pins 1.16.2), AWS provider `~> 6.0`, Terragrunt 1.1.5 in CI.

## State

S3 bucket `bckt-useast2-tf-state-<env>-<account-id>`, key
`over-engineered-simple-calculator-shared-resources/<env>/api-hostname/terraform.tfstate`, encrypted, native S3 locking.
`root.hcl` normalizes backslashes so a local run on Windows uses the same key as the CI.

## Configuration

GitHub environment `dev` or `prod`:

| Kind | Name | Use |
|---|---|---|
| Secret | `ROLE_ARN` | Deployment role assumed through OIDC |
| Secret | `AWS_ACCOUNT_ID` | The workflow checks the credentials point to this account |
| Secret | `SUPPORT_EMAIL` | Recipient of the expiry alerts |
| Variable | `AWS_REGION` | Must equal `aws_region` in `env.hcl` |
| Variable | `ROUTE53_ZONE_ID` | Hosted zone of the web domain, in the environment's account |
| Variable | `API_WEIGHT_SLS`, `API_WEIGHT_ECS`, `API_WEIGHT_EKS`, `API_WEIGHT_EC2` | Weights 0-255; unset or non-numeric counts as 0; at least one must be set |

For local runs, an untracked `secrets.yml` next to `env.hcl` can provide the same names.

## Workflows

- **CI** (`ci.yml`; pull requests to `develop` and `main`, and reused by the deploys): `terraform fmt -check`,
  `terragrunt hcl fmt --check`, mandatory tags present and `repo-name` equal to the repository name, S3 backend
  present, `terraform validate` of the module.
- **Deploy DEV / Deploy PROD:** run CI, then the reusable `deploy.yml`. Triggered by a push to `develop` / `main` that
  changes `environments/**`, `modules/**` or the workflows, or by hand. `deploy.yml` validates the branch and the
  configuration, assumes the role, checks the account, runs `terragrunt plan -out=tfplan`, **refuses any delete or
  replacement** by reading the plan JSON with `scripts/plan_guard.py check` (one exception, below), applies the same
  saved plan and writes the active weights in the job summary. One apply at a time per environment (concurrency group).
- **Guard rule (changed 2026-10-13):** a plan is refused if it deletes or replaces anything, except a pure delete
  (actions exactly `["delete"]`) of an `aws_route53_record.api[...]` whose `set_identifier` is not kept by any other
  record of the plan: a backend that was destroyed and no longer publishes its SSM parameters. The certificate, the
  validation records, the alarms, the zone and any replacement stay protected. The retired backends are listed in the
  job summary. The `weights_guard` precondition is unchanged. Tests: `python -m unittest discover -s scripts/tests`
  (also a CI job).
- A guard failure means the change needs a reviewed manual run outside the CI.
- **Drift check DEV** (`drift-check.yml`; daily 06:17 UTC and by hand, dev only): same role, account check and
  variables as the deploy; runs `terragrunt plan -detailed-exitcode` and never applies. It fails the job (GitHub
  emails the owner) when the plan is not empty or errors, and the summary classifies the plan: backend retired,
  backend new, weights changed, other drift. A failed weights guard shows as a plan error. It plans the `develop`
  branch.
- GitHub Actions must be enabled on the repository for a push to trigger anything.

## Switch weights

1. Change `API_WEIGHT_<NAME>` in the GitHub environment (Settings, Environments).
2. Run the workflow **Deploy DEV** (or **Deploy PROD**) by hand from `develop` (or `main`).
3. Read the job summary: it lists the weights of the published backends.

DNS caches converge within the target TTL. The module fails the plan if all published backends have weight 0.

## Add a backend

1. The backend publishes `/oecalc/<env>/api-backends/<name>/dns-name` and `.../hosted-zone-id`, and reads
   `/oecalc/<env>/api-certificate-arn`.
2. Add one entry to `api_backends` in `environments/dev/env.hcl` and `environments/prod/env.hcl`, copying the
   existing lines (`API_WEIGHT_<NAME>`).
3. Add `API_WEIGHT_<NAME>: ${{ vars.API_WEIGHT_<NAME> }}` to the `env` block of `.github/workflows/deploy.yml` and to the
   "at least one weight" check.
4. Create the GitHub variable in each environment and deploy. Names use lowercase letters, digits and hyphens.

## Retire a backend

1. Destroy the backend (its SSM parameters under `/oecalc/<env>/api-backends/<name>/` disappear).
2. Run **Deploy DEV** (or **Deploy PROD**) of this repository, or wait for the daily drift check to flag it and then run
   the deploy. The module drops the weighted record of that backend and the guard allows exactly that delete.
3. If the remaining backends all have weight 0, the weights guard fails: set `API_WEIGHT_<NAME>` of a remaining one first.

## `ai` host (AI backend)

The AI backend (`over-engineered-simple-calculator-ai-version`) exposes a REST API via custom domain `ai.<web domain>` in `dev` (CloudFormation stack `oecalc-shared-api-hostname-dev`, parameter `AiHostEnabled=true`). The resources are managed independently from the weighted API backends (`sls`, `ecs`, `eks`, `ec2`).

### SSM contracts

| Parameter | Type | Owner / Writer | Reader | Description |
|---|---|---|---|---|
| `/oecalc/<env>/ai-certificate-arn` | String (Standard) | `shared-resources` | AI backend | ARN of the dedicated regional ACM certificate `ai.<web domain>`. Created after issuance. Identifier, not a secret. If missing or empty, the `ai` host is not yet deployed in that environment. |
| `/oecalc/<env>/api-backends/ai/dns-name` | String (Standard) | AI backend | `shared-resources` | Regional domain name of the API Gateway custom domain. Contains `none` while custom domain is disabled. |
| `/oecalc/<env>/api-backends/ai/hosted-zone-id` | String (Standard) | AI backend | `shared-resources` | Hosted zone ID of the regional API Gateway target. Contains `none` while custom domain is disabled. |
| `/oecalc/<env>/api-backends/ai/api-id` | String (Standard) | AI backend | (Information only) | Published by AI backend; not consumed by `shared-resources`. |

### The `none` sentinel and target guards

While the AI backend runs without a custom domain, it publishes the sentinel string `none` in both `dns-name` and `hosted-zone-id`. To prevent invalid Route 53 alias records:
- **CloudFormation condition:** `IsAiRecordEnabled` evaluates to `true` only when `AiHostEnabled` is `true` AND neither `AiDnsName` nor `AiHostedZoneId` is empty (`''`) or equal to `none`.
- **Deploy helper (`.github/workflows-local/lib/cfn_local.py`):** Normalizes `none` (case-insensitive, trimmed) to empty. If values are provided, it strictly checks syntax (DNS name contains dot; hosted zone ID matches `^Z[A-Z0-9]+$`) and halts before calling AWS if malformed.
- **Independence:** `ai` is excluded from the weighted backend tuple (`BACKENDS`); it is never included in weighted records or `WeightsGuard`.

### Route 53 alias record

When published with valid targets, `AiRecord` creates a simple `A` alias pointing to the regional API Gateway domain target with `EvaluateTargetHealth: false`. It is unweighted and completely isolated from the weighted API routing.

### 4-step deployment sequence

To prevent circular dependencies between `shared-resources` and `over-engineered-simple-calculator-ai-version`:

1. **Step 1 (shared-resources):** Deploy CloudFormation stack with `AiHostEnabled=true` in `dev`. ACM requests `ai.<web domain>`, validates via Route 53 CNAME, creates parameter `/oecalc/<env>/ai-certificate-arn`, and creates two 30d/7d CloudWatch expiry alarms. `AiRecord` is skipped because the AI backend target is still `none`.
2. **Step 2 (AI backend repo):** Verify that the certificate status in ACM is `ISSUED`. Deploy the AI backend stack with `EnableCustomDomain=true`, `CertificateArn` read from `/oecalc/<env>/ai-certificate-arn`, and `CustomDomainName=ai.<web domain>`. The backend creates its API Gateway domain name and updates the SSM target parameters with the real DNS name and hosted zone ID.
3. **Step 3 (shared-resources):** Re-run deploy on `shared-resources`. The deploy script detects the published targets. The change set contains exactly one Add: `AiRecord`.
4. **Step 4 (Verification):** Perform read-only verification: `ai.<web domain>` resolves to API Gateway; TLS handshake presents the `ai` certificate; an unauthenticated `POST` returns `401 Unauthorized`; and CloudWatch alarms remain green.

### Retiring or disabling the `ai` host

1. If the AI custom domain is disabled or destroyed, the AI backend resets its target parameters to `none` (or deletes them).
2. Deploying `shared-resources` produces a change set with a single removal: `AiRecord`. The change set guard explicitly permits removing `AiRecord` while protecting the certificate, parameter, and alarms from unexpected deletion or replacement.

## Move resources between states

See [migrations](migrations/README.md). Summary: import in the new owner, then `removed` with `destroy = false` in the
old one, with a plan that has no delete and no replacement at every step.

## Local plan

From `environments/<env>/api-hostname`, run `terragrunt plan` with the read-only profile of the environment
(`tf-local-admin-dev` or `tf-local-admin-prod`) and the variables above exported. Before pushing, search the output for
`-/+`, `forces replacement` and `must be replaced`.
