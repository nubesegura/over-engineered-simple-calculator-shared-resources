# Technical guide

## Layout

| Path | Content |
|---|---|
| `modules/api-hostname/` | `main.tf` (certificate, validation, SSM), `records.tf` (backend registry, weights guard, records), `monitoring.tf` (topic, policy, subscription, alarms), `variables.tf`, `outputs.tf`, `versions.tf`, `imports.tf` (temporary, see migrations) |
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
  replacement** by reading the plan JSON (`resource_changes` with action `delete`), applies the same saved plan and
  writes the active weights in the job summary. One apply at a time per environment (concurrency group).
- A guard failure means the change needs a reviewed manual run outside the CI.
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

## Move resources between states

See [migrations](migrations/README.md). Summary: import in the new owner, then `removed` with `destroy = false` in the
old one, with a plan that has no delete and no replacement at every step.

## Local plan

From `environments/<env>/api-hostname`, run `terragrunt plan` with the read-only profile of the environment
(`tf-local-admin-dev` or `tf-local-admin-prod`) and the variables above exported. Before pushing, search the output for
`-/+`, `forces replacement` and `must be replaced`.
