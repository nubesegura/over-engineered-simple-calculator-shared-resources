# Terragrunt environments

Terragrunt units for the shared resources (API edge: certificate, weighted records, expiry alarms).

```
aws/terraform/environments/
├── root.hcl                 # provider, remote state, naming inputs
├── common/                  # unit configurations shared by every environment (module source)
├── dev/  (prod/ is identical)
│   ├── env.hcl              # environment settings (region default, domain, alerts, backends)
│   ├── _global/             # non-regional units (none yet)
│   └── us-east-2/
│       ├── region.hcl       # locals { aws_region = "us-east-2" }
│       └── edge/api-hostname/terragrunt.hcl
```

## Region

`root.hcl` reads `region.hcl` when present and falls back to `aws_region` in `env.hcl`.

## State key

`over-engineered-simple-calculator-shared-resources/<env>/<region>/<group>/<unit>/terraform.tfstate`
(for example `.../prod/us-east-2/edge/api-hostname/terraform.tfstate`), in the per-environment state bucket.

## Local secrets

An untracked `secrets.yml` next to `env.hcl` (`<env>/secrets.yml`, git-ignored) is used only when running Terragrunt by hand.
